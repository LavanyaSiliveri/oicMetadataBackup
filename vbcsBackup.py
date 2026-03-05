"""
vbcsBackup.py — Visual Builder Cloud Service (VBCS) Application Backup.

Lists all VBCS applications via the Builder Resources API, exports each
application as a zip archive, and optionally exports every Business Object's
data as a CSV file.  All artifacts are uploaded to OCI Object Storage.

APIs used
---------
List applications    : GET /ic/builder/resources/application/applist
Export app archive   : GET /ic/builder/design/{appId}/{version}/resources/application
List business objects: GET /ic/builder/design/{appId}/{version}/resources/application/businessObjects
Export BO data (CSV) : GET /ic/builder/design/{appId}/{version}/resources/data/{boName}/collection
                           with Accept: text/csv header

Required Vault secret keys
--------------------------
VBCS_HOST             : VBCS hostname (often the same as OIC_API_HOST for OIC Gen 3)
                        e.g.  design.integration.<region>.ocp.oraclecloud.com
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
                   When true, each Business Object's rows are exported as CSV.
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

VBCS_APPLIST_PATH = "/ic/builder/resources/application/applist"
VBCS_DESIGN_BASE  = "/ic/builder/design"


# ─── List Applications ────────────────────────────────────────────────────────


def list_vbcs_applications(config, access_token):
    """
    Return all VBCS application entries from the applist API.

    Each entry contains at minimum:
      id              — application identifier used in design-time URLs
      applicationName — human-readable name
      version         — version string (e.g. "1.0")
      url             — relative design-time URL
    """
    url = f"https://{config['VBCS_HOST']}{VBCS_APPLIST_PATH}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        apps = resp.json().get("items", [])
        logger.info(f"Found {len(apps)} VBCS application(s).")
        return apps
    except Exception as e:
        logger.error(f"Failed to list VBCS applications: {e}")
        return []


# ─── Export Application Archive ───────────────────────────────────────────────


def export_vbcs_application(config, access_token, app_id, version):
    """
    Download the full VBCS application as a zip archive.

    The export endpoint returns the design-time source of the entire
    application (pages, flows, business objects, service connections, etc.).

    Returns bytes on success, None on failure.
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"{VBCS_DESIGN_BASE}/{app_id}/{version}/resources/application"
    )
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/zip",
            },
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.content
        logger.info(f"Exported VBCS app: {app_id}/{version} ({len(content):,} bytes)")
        return content
    except Exception as e:
        logger.error(f"Failed to export VBCS app {app_id}/{version}: {e}")
        return None


# ─── Business Objects ─────────────────────────────────────────────────────────


def list_business_objects(config, access_token, app_id, version):
    """
    Return the list of Business Object names defined in a VBCS application.

    Each entry typically contains:
      name       — BO identifier used in data URLs
      label      — human-readable label
      fields     — array of field definitions
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"{VBCS_DESIGN_BASE}/{app_id}/{version}"
        f"/resources/application/businessObjects"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        bos = resp.json().get("items", resp.json() if isinstance(resp.json(), list) else [])
        logger.info(f"  {app_id}: found {len(bos)} business object(s).")
        return bos
    except Exception as e:
        logger.error(f"Failed to list business objects for {app_id}/{version}: {e}")
        return []


def export_business_object_data(config, access_token, app_id, version, bo_name):
    """
    Export all rows of a Business Object as CSV.

    Uses the VBCS data REST API with Accept: text/csv.
    The ?limit parameter is set high to retrieve all records in a single call;
    for very large BOs consider adding pagination.

    Returns CSV bytes on success, None on failure.
    """
    url = (
        f"https://{config['VBCS_HOST']}"
        f"{VBCS_DESIGN_BASE}/{app_id}/{version}"
        f"/resources/data/{bo_name}/collection"
    )
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "text/csv",
            },
            params={"limit": 100000},
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.content
        logger.info(f"  Exported BO data: {app_id}/{bo_name} ({len(content):,} bytes)")
        return content
    except Exception as e:
        logger.error(f"Failed to export BO data {app_id}/{bo_name}: {e}")
        return None


# ─── Orchestrator ─────────────────────────────────────────────────────────────


def run_backup(secret_ocid):
    """
    End-to-end VBCS backup:
      1. Load config from OCI Vault
      2. Obtain OAuth2 token (VBCS credentials fall back to OIC credentials)
      3. List all VBCS applications
      4. For each application:
           a. Export application archive → {ts}/vbcs/{appId}_{version}.zip
           b. If BACKUP_VBCS_DATA=true:
                - List all Business Objects
                - Export each BO's data → {ts}/vbcs/{appId}/data/{boName}.csv
      5. Notify on partial or total failure via ONS

    Returns a summary dict.
    """
    config = get_config_from_vault(secret_ocid)

    backup_data = config.get("BACKUP_VBCS_DATA", "false").lower() == "true"
    namespace   = config["OBJ_STORAGE_NAMESPACE"]
    bucket      = config["OBJ_STORAGE_BUCKET"]
    ts          = backup_timestamp()

    # OAuth2 — VBCS credentials fall back to OIC if not explicitly set
    try:
        access_token = get_access_token(config, prefix="VBCS")
    except Exception as e:
        msg = f"Failed to obtain OAuth2 access token for VBCS backup: {e}"
        logger.error(msg)
        send_failure_notification(config, msg, subject="VBCS Backup Failed - Auth Error")
        return {"status": "FAILED", "error": msg}

    apps = list_vbcs_applications(config, access_token)
    if not apps:
        return {"status": "COMPLETED", "succeeded": 0, "failed": 0, "details": []}

    succeeded = []
    failed    = []

    for app in apps:
        app_id   = app.get("id", app.get("applicationName", "unknown"))
        app_name = app.get("applicationName", app_id)
        version  = app.get("version", "1.0")
        safe_ver = version.replace(".", "_")

        # ── Application archive (zip) ─────────────────────────────────────────
        archive = export_vbcs_application(config, access_token, app_id, version)
        if archive:
            obj_name = f"vbcs-backup/{ts}/{app_id}_{safe_ver}.zip"
            upload_to_object_storage(namespace, bucket, obj_name, archive, content_type="application/zip")
            app_result = {
                "app": app_name,
                "id": app_id,
                "version": version,
                "archive": obj_name,
                "businessObjects": [],
            }
            succeeded.append(app_result)

            # ── Business Object data (CSV) ────────────────────────────────────
            if backup_data:
                bos = list_business_objects(config, access_token, app_id, version)
                for bo in bos:
                    bo_name = bo.get("name", bo) if isinstance(bo, dict) else str(bo)
                    csv_data = export_business_object_data(
                        config, access_token, app_id, version, bo_name
                    )
                    if csv_data:
                        csv_obj = f"vbcs-backup/{ts}/{app_id}/data/{bo_name}.csv"
                        upload_to_object_storage(namespace, bucket, csv_obj, csv_data, content_type="text/csv")
                        app_result["businessObjects"].append({"bo": bo_name, "object": csv_obj})
                    else:
                        logger.warning(f"Skipping BO data export for {app_id}/{bo_name}")
        else:
            failed.append({"app": app_name, "id": app_id, "version": version})

    total = len(succeeded) + len(failed)

    if failed:
        msg = (
            f"VBCS backup completed with {len(failed)} failure(s) out of {total} application(s).\n"
            f"Failed: {', '.join(f['app'] for f in failed)}"
        )
        send_failure_notification(config, msg, subject="VBCS Backup - Partial Failure")
        logger.warning(msg)
    else:
        logger.info(f"VBCS backup completed. {len(succeeded)}/{total} application(s) backed up.")

    return {
        "status": "COMPLETED" if not failed else "PARTIAL",
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": succeeded + failed,
    }
