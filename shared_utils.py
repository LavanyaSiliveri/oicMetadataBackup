"""
shared_utils.py — Common helpers shared by oicMetadataBackup, vbcsBackup, and opaBackup.

Provides:
  backup_timestamp()           — UTC timestamp string for folder/file naming
  get_config_from_vault()      — Load JSON config from an OCI Vault secret
  get_access_token()           — OAuth2 client-credentials token from IDCS
  get_object_storage_client()  — Resource-principal-aware OCI Object Storage client
  upload_to_object_storage()   — Upload bytes/str to OCI Object Storage
  send_failure_notification()  — Publish a message to an ONS topic
"""

import base64
import json
import logging
from base64 import b64encode
from datetime import datetime, timezone

import oci
import requests

logger = logging.getLogger(__name__)


# ─── Timestamp ────────────────────────────────────────────────────────────────


def backup_timestamp():
    """Return a UTC timestamp string suitable for folder/file names."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


# ─── OCI Client Factories ─────────────────────────────────────────────────────


def _resource_principal_or_file(service_client_cls, **kwargs):
    """
    Try Resource Principal first (when running as an OCI Function).
    Fall back to ~/.oci/config for local development.
    """
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return service_client_cls(config={}, signer=signer, **kwargs)
    except Exception:
        config = oci.config.from_file("~/.oci/config")
        return service_client_cls(config, **kwargs)


def get_secrets_client():
    return _resource_principal_or_file(oci.secrets.SecretsClient)


def get_object_storage_client():
    return _resource_principal_or_file(oci.object_storage.ObjectStorageClient)


def get_ons_client():
    return _resource_principal_or_file(oci.ons.NotificationDataPlaneClient)


def get_integration_client():
    return _resource_principal_or_file(oci.integration.IntegrationInstanceClient)


# ─── Vault ────────────────────────────────────────────────────────────────────


def get_config_from_vault(secret_ocid):
    """
    Load and JSON-parse a secret from OCI Vault.

    The secret must be stored as a plain-text JSON object.
    See README for the full schema used by each backup module.
    """
    client = get_secrets_client()
    bundle = client.get_secret_bundle(secret_ocid)
    encoded = bundle.data.secret_bundle_content.content
    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded)


# ─── OAuth2 ───────────────────────────────────────────────────────────────────


def get_access_token(config, prefix="OIC"):
    """
    Obtain an OAuth2 client-credentials access token from IDCS.

    Reads credentials using the given prefix (e.g. "OIC", "OPA", "VBCS").
    Falls back to the "OIC_*" keys if the prefixed key is absent, so that
    a single confidential app can authenticate to all three services when
    they share an IDCS domain.

    Expected config keys (PREFIX = OIC | OPA | VBCS):
      {PREFIX}_CLIENT_ID
      {PREFIX}_CLIENT_SECRET
      {PREFIX}_IDCS_TOKEN_URL
      {PREFIX}_SCOPE
    """
    def _get(key):
        return config.get(f"{prefix}_{key}", config.get(f"OIC_{key}", ""))

    client_id     = _get("CLIENT_ID")
    client_secret = _get("CLIENT_SECRET")
    token_url     = _get("IDCS_TOKEN_URL")
    scope         = _get("SCOPE")

    if not all([client_id, client_secret, token_url, scope]):
        raise ValueError(
            f"Missing OAuth2 credentials for prefix '{prefix}'. "
            "Ensure CLIENT_ID, CLIENT_SECRET, IDCS_TOKEN_URL and SCOPE are set in the Vault secret."
        )

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {b64encode(f'{client_id}:{client_secret}'.encode()).decode()}",
    }
    data = {"grant_type": "client_credentials", "scope": scope}

    resp = requests.post(token_url, headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ─── Object Storage ───────────────────────────────────────────────────────────


def upload_to_object_storage(namespace, bucket, obj_name, data, content_type=None):
    """
    Upload data to OCI Object Storage.

    data may be bytes, str, or a file-like object.
    content_type is inferred from obj_name extension if not provided.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    if content_type is None:
        if obj_name.endswith(".csv"):
            content_type = "text/csv"
        elif obj_name.endswith(".json"):
            content_type = "application/json"
        elif obj_name.endswith(".zip"):
            content_type = "application/zip"
        else:
            content_type = "application/octet-stream"

    client = get_object_storage_client()
    client.put_object(
        namespace_name=namespace,
        bucket_name=bucket,
        object_name=obj_name,
        put_object_body=data,
        content_type=content_type,
    )
    size = len(data) if isinstance(data, (bytes, bytearray)) else "?"
    logger.info(f"Uploaded: oci://{bucket}/{obj_name} ({size} bytes)")


# ─── Notifications ────────────────────────────────────────────────────────────


def send_failure_notification(config, message, subject="Backup Notification"):
    """
    Publish a notification to the ONS topic defined in config['ONS_TOPIC_OCID'].
    Logs but does not raise on failure so the backup result is still returned.
    """
    topic_ocid = config.get("ONS_TOPIC_OCID", "").strip()
    if not topic_ocid:
        return
    try:
        client = get_ons_client()
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
