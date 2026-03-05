"""
vbcsBackup.py — Visual Builder (VBCS) Application Backup.

Lists all VBCS applications via the VB REST API, then exports each
application archive and uploads it to OCI Object Storage.

This supplements the OIC service-instance export (which backs up the
app structure but NOT business object data).  This module exports each
VBCS app as a standalone archive (.zip) that includes BO schemas and
optionally BO data (CSV + JSON per entity).

APIs used
---------
List apps   : GET  /ic/builder/resources/application/applist
Export app  : GET  /ic/builder/design/{appId}/{version}/resources/application/archive
BO data     : GET  /{scope}/{appId}/{version}/resources/datamgr/export

Required Vault secret keys
--------------------------
VBCS_HOST             : VBCS hostname (often same as OIC_API_HOST for OIC Gen 3)
                        e.g. design.integration.<region>.ocp.oraclecloud.com
OBJ_STORAGE_NAMESPACE : OCI Object Storage tenancy namespace
OBJ_STORAGE_BUCKET    : Bucket name for backup archives

Optional Vault secret keys (fall back to OIC_* equivalents if absent)
----------------------------------------------------------------------
VBCS_CLIENT_ID        : OAuth2 client ID    (defaults to OIC_CLIENT_ID)
VBCS_CLIENT_SECRET    : OAuth2 client secret (defaults to OIC_CLIENT_SECRET)
VBCS_IDCS_TOKEN_URL   : IDCS token endpoint  (defaults to OIC_IDCS_TOKEN_URL)
VBCS_SCOPE            : OAuth2 scope for VBCS (defaults to OIC_SCOPE)

Backup control flags in Vault secret
-------------------------------------
BACKUP_VBCS_DATA : "true" / "false"  (default: false)
                   Exports all business-object data per app as a single ZIP
                   via the datamgr/export endpoint.
"""

import logging

import requests

from shared_utils import (
    backup_timestamp,
    get_access_token,
    get_config_from_vault,
    get_instance_status,
    send_failure_notification,
    upload_to_object_storage,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_APPS = 100   # safety cap passed as count= to the applist query


# ─── List Applications ────────────────────────────────────────────────────────


def list_vbcs_applications(config, access_token):
    """
    Retrieve all VBCS applications from the applist endpoint.

    Query params request all states (development, stage, live) and only
    the latest version of each app.
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"/ic/builder/resources/application/applist"
        f"?vbcsProjectType=t&latestVersions=t"
        f"&development=t&stage=t&live=t"
        f"&offset=0&count={MAX_APPS}"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        apps = resp.json().get("items", resp.json().get("applications", []))
        logger.info(f"Found {len(apps)} VBCS application(s).")
        return apps
    except Exception as e:
        logger.error(f"Failed to list VBCS applications: {e}")
        return []


# ─── Export Application Archive ───────────────────────────────────────────────


def export_vbcs_app_archive(config, access_token, app_id, version):
    """
    Export a single VBCS application as a binary zip archive.

    Returns raw bytes on success, None on failure.
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"/ic/builder/design/{app_id}/{version}"
        f"/resources/application/archive"
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
        logger.info(f"Exported VBCS app archive: {app_id}/{version} ({len(content):,} bytes)")
        return content
    except Exception as e:
        logger.error(f"Failed to export VBCS app {app_id}/{version}: {e}")
        return None


# ─── Business Object Data ─────────────────────────────────────────────────────


def export_vbcs_bo_data(config, access_token, app_id, version, scope="design"):
    """
    Export all business-object data for a VBCS application as a single ZIP
    containing one CSV file per entity, using the datamgr/export endpoint.

    scope must be:
      'design'     — for development/draft apps
      'deployment' — for staged or live apps
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"/ic/builder/{scope}/{app_id}/{version}"
        f"/resources/datamgr/export"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            stream=True,
            timeout=120,
        )
        if resp.status_code == 200:
            content = resp.content
            logger.info(f"Exported BO data: {app_id}/{version} ({len(content):,} bytes)")
            return content
        else:
            logger.warning(
                f"BO data export for {app_id} returned {resp.status_code} "
                "(app may have no business objects)"
            )
            return None
    except Exception as e:
        logger.error(f"Failed to export BO data for {app_id}/{version}: {e}")
        return None


# ─── Orchestrator ─────────────────────────────────────────────────────────────


def run_backup(secret_ocid):
    """
    End-to-end VBCS backup:
      1. Load config from OCI Vault
      2. Check OIC instance is ACTIVE (VBCS lives inside OIC)
      3. Obtain OAuth2 token (VBCS credentials fall back to OIC credentials)
      4. List all VBCS applications
      5. For each application:
           a. Export app archive (.zip)  → vbcs-backup/{ts}/{appId}_{version}_app.zip
           b. If BACKUP_VBCS_DATA=true:
              Export BO data ZIP via datamgr/export (scope: design or deployment)
              → vbcs-backup/{ts}/{appId}_{version}_bo_data.zip
      6. Notify on partial or total failure via ONS

    Returns a summary dict.
    """
    config = get_config_from_vault(secret_ocid)

    # Instance health check — VBCS lives inside OIC
    if config.get("OIC_INSTANCE_OCID"):
        status = get_instance_status(config["OIC_INSTANCE_OCID"])
        if status == "INACTIVE":
            msg = "OIC instance is INACTIVE. VBCS backup skipped."
            logger.warning(msg)
            send_failure_notification(config, msg, subject="VBCS Backup Skipped - Instance Inactive")
            return {"status": "SKIPPED", "reason": msg}

    # OAuth2 — VBCS credentials fall back to OIC if not explicitly set
    try:
        access_token = get_access_token(config, prefix="VBCS")
    except Exception as e:
        msg = f"Failed to obtain OAuth2 access token for VBCS backup: {e}"
        logger.error(msg)
        send_failure_notification(config, msg, subject="VBCS Backup Failed - Auth Error")
        return {"status": "FAILED", "error": msg}

    backup_data = config.get("BACKUP_VBCS_DATA", "false").lower() == "true"
    namespace   = config["OBJ_STORAGE_NAMESPACE"]
    bucket      = config["OBJ_STORAGE_BUCKET"]
    ts          = backup_timestamp()

    apps = list_vbcs_applications(config, access_token)
    if not apps:
        msg = "No VBCS applications found or failed to list applications."
        logger.warning(msg)
        return {"status": "COMPLETED", "succeeded": 0, "failed": 0, "details": []}

    succeeded = []
    failed    = []

    for app in apps:
        app_id     = app.get("id", app.get("appId", "unknown"))
        version    = app.get("version", "1.0")
        app_status = app.get("state", app.get("status", "unknown"))

        # ── Application archive (.zip) ────────────────────────────────────────
        archive = export_vbcs_app_archive(config, access_token, app_id, version)
        if archive:
            obj_name = f"vbcs-backup/{ts}/{app_id}_{version}_app.zip"
            upload_to_object_storage(namespace, bucket, obj_name, archive)
            app_result = {"app": app_id, "version": version, "archive": obj_name}

            # ── BO data ZIP (optional) ────────────────────────────────────────
            if backup_data:
                scope   = "deployment" if app_status in ("live", "LIVE", "STAGED") else "design"
                bo_data = export_vbcs_bo_data(config, access_token, app_id, version, scope)
                if bo_data:
                    bo_obj = f"vbcs-backup/{ts}/{app_id}_{version}_bo_data.zip"
                    upload_to_object_storage(namespace, bucket, bo_obj, bo_data)
                    app_result["boData"] = bo_obj

            succeeded.append(app_result)
        else:
            failed.append({"app": app_id, "version": version})

    total = len(succeeded) + len(failed)

    if failed:
        msg = (
            f"VBCS backup completed with {len(failed)} failure(s) out of {total} application(s).\n"
            f"Failed: {', '.join(f['app'] for f in failed)}"
        )
        send_failure_notification(config, msg, subject="VBCS Backup Partial Failure")
        logger.warning(msg)
    else:
        logger.info(f"VBCS backup completed successfully for {len(apps)} application(s).")

    return {
        "status": "COMPLETED" if not failed else "PARTIAL",
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": succeeded + failed,
    }
