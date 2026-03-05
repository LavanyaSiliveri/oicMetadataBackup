import base64
import json
import logging
import time
from base64 import b64encode
from datetime import datetime

import oci
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXPORT_API_PATH = "/ic/api/common/v1/exportServiceInstanceArchive"
POLL_INTERVAL_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 270  # stay inside the 300s function timeout


# ─── OCI Client Helpers ────────────────────────────────────────────────────────


def _get_secrets_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.secrets.SecretsClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file("~/.oci/config")
        return oci.secrets.SecretsClient(config)


def _get_integration_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.integration.IntegrationInstanceClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file("~/.oci/config")
        return oci.integration.IntegrationInstanceClient(config)


def _get_ons_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.ons.NotificationDataPlaneClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file("~/.oci/config")
        return oci.ons.NotificationDataPlaneClient(config)


# ─── Vault Config Loader ──────────────────────────────────────────────────────


def get_config_from_vault(secret_ocid):
    """
    Load and parse the JSON config stored as an OCI Vault secret.

    Expected secret JSON structure:
    {
      "OIC_CLIENT_ID":       "<OAUTH_APP_CLIENT_ID>",
      "OIC_CLIENT_SECRET":   "<OAUTH_APP_CLIENT_SECRET>",
      "OIC_IDCS_TOKEN_URL":  "https://idcs-xxx.identity.oraclecloud.com/oauth2/v1/token",
      "OIC_SCOPE":           "https://<OIC_ID>.integration.<REGION>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all",
      "OIC_INSTANCE_NAME":   "<OIC_INSTANCE_NAME>",
      "OIC_INSTANCE_OCID":   "<OIC_INSTANCE_OCID>",
      "OIC_API_HOST":        "design.integration.<REGION>.ocp.oraclecloud.com",
      "SWIFT_URL":           "https://swiftobjectstorage.<REGION>.oraclecloud.com/v1/<NAMESPACE>/<BUCKET>",
      "SWIFT_USER":          "<TENANCY>/<USERNAME>",
      "SWIFT_PASSWORD":      "<AUTH_TOKEN>",
      "ONS_TOPIC_OCID":      "<ONS_TOPIC_OCID>"
    }
    """
    client = _get_secrets_client()
    bundle = client.get_secret_bundle(secret_ocid)
    encoded = bundle.data.secret_bundle_content.content
    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded)


# ─── OIC Instance Status ──────────────────────────────────────────────────────


def get_instance_status(instance_ocid):
    """Return the OIC instance lifecycle_state (e.g. ACTIVE, INACTIVE)."""
    client = _get_integration_client()
    instance = client.get_integration_instance(instance_ocid)
    return instance.data.lifecycle_state


# ─── OAuth2 Token ─────────────────────────────────────────────────────────────


def get_access_token(config):
    """Obtain an OAuth2 access token from IDCS using the client credentials grant."""
    basic_auth = b64encode(
        f"{config['OIC_CLIENT_ID']}:{config['OIC_CLIENT_SECRET']}".encode()
    ).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {basic_auth}",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": config["OIC_SCOPE"],
    }

    resp = requests.post(config["OIC_IDCS_TOKEN_URL"], headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ─── Export Service Instance Archive ──────────────────────────────────────────


def trigger_export(config, access_token):
    """
    POST to exportServiceInstanceArchive.

    OIC writes the archive directly to the Swift/Object Storage URL —
    no data flows through this function.

    Returns the job_id string on success, raises on failure.
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
            "storageUrl": config["SWIFT_URL"],
            "storageUser": config["SWIFT_USER"],
            "storagePassword": config["SWIFT_PASSWORD"],
        },
    }

    export_url = (
        f"https://{config['OIC_API_HOST']}{EXPORT_API_PATH}"
        f"?integrationInstance={config['OIC_INSTANCE_NAME']}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(export_url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()

    job_id = resp.json()["jobId"]
    logger.info(f"Export job started: jobId={job_id}, jobName={job_name}")
    return job_id


# ─── Poll Export Job Status ───────────────────────────────────────────────────


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

    # Brief initial pause to let OIC register the job
    time.sleep(10)
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(
                f"Export job {job_id} did not complete within {timeout_seconds}s."
            )

        resp = requests.get(status_url, headers=headers, timeout=30)
        resp.raise_for_status()
        status = resp.json().get("overallStatus", "UNKNOWN")
        logger.info(f"Job {job_id} — status: {status} (elapsed: {elapsed:.0f}s)")

        if status in ("COMPLETED", "FAILED"):
            return status

        time.sleep(POLL_INTERVAL_SECONDS)


# ─── Notifications ────────────────────────────────────────────────────────────


def send_notification(config, message, subject="OIC Backup Notification"):
    """Publish a message to the ONS topic from config. Logs but does not raise."""
    topic_ocid = config.get("ONS_TOPIC_OCID", "").strip()
    if not topic_ocid:
        return
    try:
        client = _get_ons_client()
        client.publish_message(
            topic_id=topic_ocid,
            message_details=oci.ons.models.MessageDetails(
                title=subject,
                body=message,
            ),
        )
        logger.info(f"Notification sent: {subject}")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


# ─── Main Orchestration ───────────────────────────────────────────────────────


def run_backup(secret_ocid):
    """
    Full backup flow:
      1. Load config JSON from OCI Vault secret
      2. Check OIC instance is ACTIVE — skip and notify if not
      3. Obtain OAuth2 access token from IDCS
      4. POST exportServiceInstanceArchive (OIC writes directly to Object Storage)
      5. Poll until COMPLETED / FAILED / timeout
      6. Send success or failure notification via ONS

    Returns a result dict.
    """
    # 1. Config from Vault
    logger.info(f"Loading config from Vault secret: {secret_ocid}")
    config = get_config_from_vault(secret_ocid)

    # 2. OIC instance status check
    logger.info(f"Checking OIC instance status: {config['OIC_INSTANCE_OCID']}")
    instance_status = get_instance_status(config["OIC_INSTANCE_OCID"])
    logger.info(f"OIC instance lifecycle state: {instance_status}")

    if instance_status != "ACTIVE":
        msg = (
            f"OIC instance '{config['OIC_INSTANCE_NAME']}' is in {instance_status} state. "
            "Backup will not be initiated."
        )
        logger.warning(msg)
        send_notification(config, msg, subject="OIC Backup Skipped - Instance Not Active")
        return {"status": "SKIPPED", "reason": msg}

    # 3. OAuth2 token
    logger.info("Obtaining OAuth2 access token from IDCS...")
    access_token = get_access_token(config)

    # 4. Trigger export
    logger.info("Triggering exportServiceInstanceArchive...")
    job_id = trigger_export(config, access_token)

    # 5. Poll for completion
    try:
        final_status = poll_export_status(config, access_token, job_id)
    except TimeoutError as e:
        msg = str(e)
        logger.error(msg)
        send_notification(config, msg, subject="OIC Backup Failed - Timeout")
        return {"status": "TIMEOUT", "jobId": job_id, "error": msg}

    # 6. Notify
    if final_status == "COMPLETED":
        msg = (
            f"OIC metadata backup completed successfully.\n"
            f"Instance: {config['OIC_INSTANCE_NAME']}\n"
            f"Job ID: {job_id}\n"
            f"Storage: {config['SWIFT_URL']}"
        )
        logger.info(f"Backup COMPLETED: jobId={job_id}")
        send_notification(config, msg, subject="OIC Backup Completed Successfully")
    else:
        msg = (
            f"OIC metadata backup job ended with status: {final_status}\n"
            f"Instance: {config['OIC_INSTANCE_NAME']}\n"
            f"Job ID: {job_id}"
        )
        logger.error(msg)
        send_notification(config, msg, subject="OIC Backup Failed")

    return {"status": final_status, "jobId": job_id}
