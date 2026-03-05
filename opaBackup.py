"""
opaBackup.py — OCI Process Automation (OPA) Application Backup.

Lists all Process Automation applications via the OPA Design REST API,
then exports each application as an .expx archive and uploads it to
OCI Object Storage.  Also handles Decision Model (DMN) applications.

APIs used
---------
List process apps   : GET  /process/api/v1/design/applications
Export process app  : GET  /process/api/v1/design/applications/{name}/versions/{ver}/export
List decision apps  : GET  /process/api/v1/design/dmnApplications
Export decision app : GET  /process/api/v1/design/dmnApplications/{name}/versions/{ver}/export

Required Vault secret keys (in addition to shared OIC keys)
------------------------------------------------------------
OPA_HOST            : Hostname of the OPA instance
                      e.g. myinstance.process.ocp.oraclecloud.com
OBJ_STORAGE_NAMESPACE : OCI Object Storage tenancy namespace
OBJ_STORAGE_BUCKET    : Bucket name for backup archives

Optional Vault secret keys (fall back to OIC_* equivalents if absent)
----------------------------------------------------------------------
OPA_CLIENT_ID       : OAuth2 client ID for OPA (defaults to OIC_CLIENT_ID)
OPA_CLIENT_SECRET   : OAuth2 client secret  (defaults to OIC_CLIENT_SECRET)
OPA_IDCS_TOKEN_URL  : IDCS token endpoint   (defaults to OIC_IDCS_TOKEN_URL)
OPA_SCOPE           : OAuth2 scope for OPA  (defaults to OIC_SCOPE)

Backup control flags in Vault secret
-------------------------------------
EXPORT_DECISION_APPS : "true" / "false"  (default: true)
"""

import logging

import requests

from shared_utils import (
    backup_timestamp,
    get_access_token,
    get_config_from_vault,
    send_failure_notification,
    upload_to_object_storage,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPA_PROCESS_PATH = "/process/api/v1/design/applications"
OPA_DMN_PATH     = "/process/api/v1/design/dmnApplications"


# ─── List Applications ────────────────────────────────────────────────────────


def list_process_applications(config, access_token):
    """Return all OPA Process Automation application entries."""
    url = f"https://{config['OPA_HOST']}{OPA_PROCESS_PATH}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        apps = resp.json().get("items", [])
        logger.info(f"Found {len(apps)} OPA process application(s).")
        return apps
    except Exception as e:
        logger.error(f"Failed to list OPA process applications: {e}")
        return []


def list_decision_applications(config, access_token):
    """Return all OPA Decision Model (DMN) application entries."""
    url = f"https://{config['OPA_HOST']}{OPA_DMN_PATH}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        apps = resp.json().get("items", [])
        logger.info(f"Found {len(apps)} OPA decision application(s).")
        return apps
    except Exception as e:
        logger.error(f"Failed to list OPA decision applications: {e}")
        return []


# ─── Export Applications ──────────────────────────────────────────────────────


def export_process_application(config, access_token, app_name, version):
    """
    Download a Process application as an .expx binary archive.
    Returns bytes on success, None on failure.
    """
    url = (
        f"https://{config['OPA_HOST']}"
        f"{OPA_PROCESS_PATH}/{app_name}/versions/{version}/export"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.content
        logger.info(f"Exported OPA process app: {app_name}/{version} ({len(content):,} bytes)")
        return content
    except Exception as e:
        logger.error(f"Failed to export OPA process app {app_name}/{version}: {e}")
        return None


def export_decision_application(config, access_token, app_name, version):
    """
    Download a Decision Model application as an .expx binary archive.
    Returns bytes on success, None on failure.
    """
    url = (
        f"https://{config['OPA_HOST']}"
        f"{OPA_DMN_PATH}/{app_name}/versions/{version}/export"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.content
        logger.info(f"Exported OPA decision app: {app_name}/{version} ({len(content):,} bytes)")
        return content
    except Exception as e:
        logger.error(f"Failed to export OPA decision app {app_name}/{version}: {e}")
        return None


# ─── Orchestrator ─────────────────────────────────────────────────────────────


def run_backup(secret_ocid):
    """
    End-to-end OPA backup:
      1. Load config from OCI Vault
      2. Obtain OAuth2 token (OPA credentials fall back to OIC credentials)
      3. List and export all Process applications  → {ts}/opa/process/{name}_{ver}.expx
      4. List and export all Decision applications → {ts}/opa/decisions/{name}_{ver}.expx
      5. Notify on partial or total failure via ONS

    Returns a summary dict.
    """
    config = get_config_from_vault(secret_ocid)

    export_decisions = config.get("EXPORT_DECISION_APPS", "true").lower() == "true"
    namespace        = config["OBJ_STORAGE_NAMESPACE"]
    bucket           = config["OBJ_STORAGE_BUCKET"]
    ts               = backup_timestamp()

    # OAuth2 — OPA credentials fall back to OIC if not explicitly set
    try:
        access_token = get_access_token(config, prefix="OPA")
    except Exception as e:
        msg = f"Failed to obtain OAuth2 access token for OPA backup: {e}"
        logger.error(msg)
        send_failure_notification(config, msg, subject="OPA Backup Failed - Auth Error")
        return {"status": "FAILED", "error": msg}

    succeeded  = []
    failed     = []

    # ── Process Applications ──────────────────────────────────────────────────
    process_apps = list_process_applications(config, access_token)
    for app in process_apps:
        app_name = app.get("name", app.get("applicationName", "unknown"))
        version  = app.get("version", "1.0")

        archive = export_process_application(config, access_token, app_name, version)
        if archive:
            obj_name = f"opa-backup/{ts}/process/{app_name}_{version}.expx"
            upload_to_object_storage(namespace, bucket, obj_name, archive)
            succeeded.append({"type": "process", "app": app_name, "version": version, "object": obj_name})
        else:
            failed.append({"type": "process", "app": app_name, "version": version})

    # ── Decision Model Applications ───────────────────────────────────────────
    if export_decisions:
        dmn_apps = list_decision_applications(config, access_token)
        for app in dmn_apps:
            app_name = app.get("name", app.get("applicationName", "unknown"))
            version  = app.get("version", "1.0")

            archive = export_decision_application(config, access_token, app_name, version)
            if archive:
                obj_name = f"opa-backup/{ts}/decisions/{app_name}_{version}.expx"
                upload_to_object_storage(namespace, bucket, obj_name, archive)
                succeeded.append({"type": "decision", "app": app_name, "version": version, "object": obj_name})
            else:
                failed.append({"type": "decision", "app": app_name, "version": version})

    total = len(succeeded) + len(failed)

    if failed:
        msg = (
            f"OPA backup completed with {len(failed)} failure(s) out of {total} application(s).\n"
            f"Failed: {', '.join(f['app'] for f in failed)}"
        )
        send_failure_notification(config, msg, subject="OPA Backup - Partial Failure")
        logger.warning(msg)
    else:
        logger.info(f"OPA backup completed. {len(succeeded)}/{total} application(s) backed up.")

    return {
        "status": "COMPLETED" if not failed else "PARTIAL",
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": succeeded + failed,
    }
