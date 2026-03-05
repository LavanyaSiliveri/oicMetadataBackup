"""
oicMetadataBackup.py — Oracle Integration Cloud (OIC) Metadata Backup.

Uses the OIC exportServiceInstanceArchive API to trigger a full design-time
metadata export. OIC writes the archive directly to OCI Object Storage via
the Swift endpoint — no data flows through this function.

APIs used
---------
Trigger export : POST /ic/api/common/v1/exportServiceInstanceArchive
Poll status    : GET  /ic/api/common/v1/exportServiceInstanceArchive/{jobId}

Required Vault secret keys
--------------------------
OIC_CLIENT_ID        : OAuth2 client ID
OIC_CLIENT_SECRET    : OAuth2 client secret
OIC_IDCS_TOKEN_URL   : IDCS token endpoint
OIC_SCOPE            : OAuth2 scope for OIC
OIC_INSTANCE_NAME    : OIC service instance name (shown on About page)
OIC_INSTANCE_OCID    : OCID of the OIC integration instance resource
OIC_API_HOST         : OIC design-time hostname
                       e.g. design.integration.<region>.ocp.oraclecloud.com
SWIFT_URL            : Swift-compatible Object Storage URL for the archive
                       e.g. https://swiftobjectstorage.<region>.oraclecloud.com/v1/<ns>/<bucket>
SWIFT_USER           : Swift auth user  (<tenancy>/<username>)
SWIFT_PASSWORD       : Swift auth token (OCI user Auth Token)
ONS_TOPIC_OCID       : ONS topic OCID for success/failure notifications
"""

import logging
import time
from datetime import datetime

import requests

from shared_utils import (
    get_access_token,
    get_config_from_vault,
    get_integration_client,
    send_failure_notification,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXPORT_API_PATH        = "/ic/api/common/v1/exportServiceInstanceArchive"
POLL_INTERVAL_SECONDS  = 15
DEFAULT_TIMEOUT_SECONDS = 270  # stays inside the 300s function timeout


# ─── OIC Instance Status ──────────────────────────────────────────────────────


def get_instance_status(instance_ocid):
    """Return the OIC instance lifecycle_state (ACTIVE, INACTIVE, …)."""
    client   = get_integration_client()
    instance = client.get_integration_instance(instance_ocid)
    return instance.data.lifecycle_state


# ─── Export Service Instance Archive ──────────────────────────────────────────


def trigger_export(config, access_token):
    """
    POST to exportServiceInstanceArchive.

    OIC writes the full design-time archive directly to Object Storage via
    the Swift credentials in storageInfo.  Returns the job_id string.
    """
    job_name = (
        f"{config['OIC_INSTANCE_NAME']}_Backup_"
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    )

    payload = {
        "jobName": job_name,
        "exportSecurityArtifacts": False,
        "description": f"Scheduled metadata backup of OIC instance {config['OIC_INSTANCE_NAME']}",
        "storageInfo": {
            "storageUrl":      config["SWIFT_URL"],
            "storageUser":     config["SWIFT_USER"],
            "storagePassword": config["SWIFT_PASSWORD"],
        },
    }

    url = (
        f"https://{config['OIC_API_HOST']}{EXPORT_API_PATH}"
        f"?integrationInstance={config['OIC_INSTANCE_NAME']}"
    )

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    job_id = resp.json()["jobId"]
    logger.info(f"OIC export job started: jobId={job_id}, jobName={job_name}")
    return job_id


def poll_export_status(config, access_token, job_id, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """
    Poll the export job until it reaches COMPLETED or FAILED, or until timeout.
    Returns the final overallStatus string.
    """
    status_url = (
        f"https://{config['OIC_API_HOST']}{EXPORT_API_PATH}/{job_id}"
        f"?integrationInstance={config['OIC_INSTANCE_NAME']}"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # Brief pause to let OIC register the job
    time.sleep(10)
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(
                f"OIC export job {job_id} did not complete within {timeout_seconds}s."
            )

        resp = requests.get(status_url, headers=headers, timeout=30)
        resp.raise_for_status()
        status = resp.json().get("overallStatus", "UNKNOWN")
        logger.info(f"OIC job {job_id} — status: {status} (elapsed: {elapsed:.0f}s)")

        if status in ("COMPLETED", "FAILED"):
            return status

        time.sleep(POLL_INTERVAL_SECONDS)


# ─── Orchestrator ─────────────────────────────────────────────────────────────


def run_backup(secret_ocid):
    """
    End-to-end OIC metadata backup:
      1. Load config from OCI Vault
      2. Check OIC instance is ACTIVE — skip and notify if not
      3. Obtain OAuth2 access token
      4. POST exportServiceInstanceArchive (OIC writes directly to Object Storage)
      5. Poll until COMPLETED / FAILED / timeout
      6. Send success or failure notification via ONS

    Returns a result dict.
    """
    config = get_config_from_vault(secret_ocid)

    # Instance status check
    logger.info(f"Checking OIC instance status: {config['OIC_INSTANCE_OCID']}")
    instance_status = get_instance_status(config["OIC_INSTANCE_OCID"])
    logger.info(f"OIC instance lifecycle state: {instance_status}")

    if instance_status != "ACTIVE":
        msg = (
            f"OIC instance '{config['OIC_INSTANCE_NAME']}' is in {instance_status} state. "
            "Backup will not be initiated."
        )
        logger.warning(msg)
        send_failure_notification(config, msg, subject="OIC Backup Skipped - Instance Not Active")
        return {"status": "SKIPPED", "reason": msg}

    # OAuth2 token
    logger.info("Obtaining OAuth2 access token for OIC...")
    access_token = get_access_token(config, prefix="OIC")

    # Trigger export
    logger.info("Triggering exportServiceInstanceArchive...")
    job_id = trigger_export(config, access_token)

    # Poll
    try:
        final_status = poll_export_status(config, access_token, job_id)
    except TimeoutError as e:
        msg = str(e)
        logger.error(msg)
        send_failure_notification(config, msg, subject="OIC Backup Failed - Timeout")
        return {"status": "TIMEOUT", "jobId": job_id, "error": msg}

    # Notify
    if final_status == "COMPLETED":
        send_failure_notification(
            config,
            f"OIC metadata backup completed successfully.\n"
            f"Instance: {config['OIC_INSTANCE_NAME']}\n"
            f"Job ID: {job_id}\n"
            f"Storage: {config['SWIFT_URL']}",
            subject="OIC Backup Completed Successfully",
        )
    else:
        send_failure_notification(
            config,
            f"OIC export job ended with status: {final_status}\n"
            f"Instance: {config['OIC_INSTANCE_NAME']}\nJob ID: {job_id}",
            subject="OIC Backup Failed",
        )

    logger.info(f"OIC backup finished: status={final_status}, jobId={job_id}")
    return {"status": final_status, "jobId": job_id}
