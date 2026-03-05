import base64
import json
import logging
import traceback
from datetime import datetime, timezone
from urllib.parse import quote

import oci
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OIC_API_BASE = "/ic/api/integration/v1"
DEFAULT_BACKUP_PREFIX = "backups"
LIST_PAGE_SIZE = 500


# ─── OCI Client Helpers ────────────────────────────────────────────────────────


def get_object_storage_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file()
        return oci.object_storage.ObjectStorageClient(config)


def get_secrets_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.secrets.SecretsClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file()
        return oci.secrets.SecretsClient(config)


def get_ons_client():
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        return oci.ons.NotificationDataPlaneClient(config={}, signer=signer)
    except Exception:
        config = oci.config.from_file()
        return oci.ons.NotificationDataPlaneClient(config)


# ─── OCI Vault ────────────────────────────────────────────────────────────────


def get_secret_value(secret_ocid):
    """Retrieve a plain-text secret value from OCI Vault."""
    client = get_secrets_client()
    bundle = client.get_secret_bundle(secret_id=secret_ocid)
    content = bundle.data.secret_bundle_content.content
    return base64.b64decode(content).decode("utf-8")


# ─── OIC Credentials ──────────────────────────────────────────────────────────


def resolve_credentials(cfg):
    """
    Resolve OIC username and password from either Vault secrets or direct config.

    Priority:
      1. OIC_USERNAME_SECRET_OCID / OIC_PASSWORD_SECRET_OCID  (Vault — recommended)
      2. OIC_USERNAME / OIC_PASSWORD                          (direct config vars)
    """
    username_secret_ocid = cfg.get("OIC_USERNAME_SECRET_OCID", "").strip()
    password_secret_ocid = cfg.get("OIC_PASSWORD_SECRET_OCID", "").strip()

    if username_secret_ocid and password_secret_ocid:
        logger.info("Resolving OIC credentials from OCI Vault secrets.")
        username = get_secret_value(username_secret_ocid)
        password = get_secret_value(password_secret_ocid)
    else:
        logger.info("Resolving OIC credentials from function config vars.")
        username = cfg.get("OIC_USERNAME", "").strip()
        password = cfg.get("OIC_PASSWORD", "").strip()

    if not username or not password:
        raise ValueError(
            "OIC credentials not found. Provide either OIC_USERNAME_SECRET_OCID "
            "+ OIC_PASSWORD_SECRET_OCID (Vault) or OIC_USERNAME + OIC_PASSWORD "
            "(function config vars)."
        )
    return username, password


# ─── OIC REST API Helpers ─────────────────────────────────────────────────────


def _oic_session(username, password):
    """Return a requests Session configured with basic auth and JSON Accept header."""
    session = requests.Session()
    session.auth = (username, password)
    session.headers.update({"Accept": "application/json"})
    return session


def _list_all(session, base_url, path, service_instance, extra_params=None):
    """
    Paginate through an OIC list endpoint and return all items.

    OIC paginates using ?offset=&limit= query parameters.
    """
    url = f"{base_url}{OIC_API_BASE}{path}"
    params = {
        "limit": LIST_PAGE_SIZE,
        "totalResults": "true",
        "integrationInstance": service_instance,
    }
    if extra_params:
        params.update(extra_params)

    items = []
    offset = 0

    while True:
        params["offset"] = offset
        resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        page_items = data.get("items", [])
        items.extend(page_items)
        total = data.get("totalResults", len(items))
        offset += len(page_items)
        if offset >= total or not page_items:
            break

    return items


# ─── OIC Data Retrieval ───────────────────────────────────────────────────────


def list_integrations(session, oic_base_url, service_instance, include_inactive=False):
    """
    Return all integrations from OIC.

    By default only ACTIVATED integrations are returned.
    Set include_inactive=True to also include CONFIGURED / DRAFT integrations.
    """
    extra = {}
    if not include_inactive:
        extra["status"] = "ACTIVATED"

    logger.info(f"Listing integrations (include_inactive={include_inactive})...")
    integrations = _list_all(
        session, oic_base_url, "/integrations", service_instance, extra
    )
    logger.info(f"Found {len(integrations)} integration(s).")
    return integrations


def export_integration(session, oic_base_url, service_instance, integration_id):
    """
    Download an integration archive (.iar) as raw bytes.

    integration_id — the full composite ID in the form code|version
                     (will be URL-encoded automatically).
    """
    encoded_id = quote(integration_id, safe="")
    url = (
        f"{oic_base_url}{OIC_API_BASE}/integrations/{encoded_id}/archive"
        f"?integrationInstance={quote(service_instance, safe='')}"
    )
    resp = session.get(url, headers={"Accept": "application/octet-stream"}, timeout=120)
    resp.raise_for_status()
    return resp.content


def list_connections(session, oic_base_url, service_instance):
    """Return connection metadata (not credentials) for all connections."""
    logger.info("Listing connections...")
    connections = _list_all(session, oic_base_url, "/connections", service_instance)
    logger.info(f"Found {len(connections)} connection(s).")
    return connections


def list_lookups(session, oic_base_url, service_instance):
    """Return all lookup table definitions."""
    logger.info("Listing lookups...")
    lookups = _list_all(session, oic_base_url, "/lookups", service_instance)
    logger.info(f"Found {len(lookups)} lookup(s).")
    return lookups


def list_packages(session, oic_base_url, service_instance):
    """Return all package definitions."""
    logger.info("Listing packages...")
    packages = _list_all(session, oic_base_url, "/packages", service_instance)
    logger.info(f"Found {len(packages)} package(s).")
    return packages


# ─── OCI Object Storage ───────────────────────────────────────────────────────


def get_namespace(os_client):
    """Return the Object Storage namespace for this tenancy."""
    return os_client.get_namespace().data


def upload_object(os_client, namespace, bucket, object_name, data, content_type="application/octet-stream"):
    """Upload bytes or a string to OCI Object Storage."""
    if isinstance(data, str):
        data = data.encode("utf-8")
        content_type = "application/json"
    os_client.put_object(
        namespace_name=namespace,
        bucket_name=bucket,
        object_name=object_name,
        put_object_body=data,
        content_type=content_type,
    )
    logger.info(f"Uploaded: {object_name} ({len(data):,} bytes)")


# ─── Notifications ────────────────────────────────────────────────────────────


def send_notification(topic_ocid, title, message):
    """Publish a message to an OCI Notification topic. Logs but does not raise."""
    if not topic_ocid:
        return
    try:
        client = get_ons_client()
        client.publish_message(
            topic_id=topic_ocid,
            message_details=oci.ons.models.MessageDetails(title=title, body=message),
        )
        logger.info(f"Notification sent: {title}")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


# ─── Core Backup Orchestration ────────────────────────────────────────────────


def backup_oic_metadata(cfg):
    """
    Orchestrate a full OIC metadata backup.

    Reads all settings from the cfg dict (OCI Function config vars or a plain dict).
    Returns a summary dict with counts and any per-integration errors.

    Required cfg keys:
        OIC_BASE_URL               OIC host URL (no trailing slash)
        OIC_SERVICE_INSTANCE       OIC service instance name (shown on OIC About page)
        OIC_USERNAME / OIC_USERNAME_SECRET_OCID
        OIC_PASSWORD / OIC_PASSWORD_SECRET_OCID
        OBJECT_STORAGE_BUCKET_NAME OCI Object Storage bucket name

    Optional cfg keys:
        OBJECT_STORAGE_NAMESPACE   Auto-detected from OCI API if omitted
        BACKUP_PREFIX              Folder prefix in the bucket  (default: backups)
        INCLUDE_INACTIVE           Back up non-ACTIVATED integrations (default: false)
        BACKUP_CONNECTIONS         Save connection metadata JSON  (default: true)
        BACKUP_LOOKUPS             Save lookup metadata JSON      (default: true)
        BACKUP_PACKAGES            Save package list JSON         (default: true)
        NOTIFICATION_TOPIC_OCID    ONS topic OCID for alerts      (default: none)
    """
    oic_base_url = cfg.get("OIC_BASE_URL", "").rstrip("/")
    service_instance = cfg.get("OIC_SERVICE_INSTANCE", "").strip()
    bucket = cfg.get("OBJECT_STORAGE_BUCKET_NAME", "").strip()
    namespace_cfg = cfg.get("OBJECT_STORAGE_NAMESPACE", "").strip()
    backup_prefix = cfg.get("BACKUP_PREFIX", DEFAULT_BACKUP_PREFIX).strip()
    include_inactive = cfg.get("INCLUDE_INACTIVE", "false").lower() == "true"
    backup_connections = cfg.get("BACKUP_CONNECTIONS", "true").lower() == "true"
    backup_lookups = cfg.get("BACKUP_LOOKUPS", "true").lower() == "true"
    backup_packages = cfg.get("BACKUP_PACKAGES", "true").lower() == "true"
    topic_ocid = cfg.get("NOTIFICATION_TOPIC_OCID", "").strip() or None

    # Validate required config
    missing = [k for k, v in {
        "OIC_BASE_URL": oic_base_url,
        "OIC_SERVICE_INSTANCE": service_instance,
        "OBJECT_STORAGE_BUCKET_NAME": bucket,
    }.items() if not v]
    if missing:
        raise ValueError(f"Missing required config: {', '.join(missing)}")

    username, password = resolve_credentials(cfg)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    run_prefix = f"{backup_prefix}/{timestamp}"
    logger.info(f"Starting OIC metadata backup -> oci://{bucket}/{run_prefix}/")

    session = _oic_session(username, password)
    os_client = get_object_storage_client()
    namespace = namespace_cfg or get_namespace(os_client)

    summary = {
        "timestamp": timestamp,
        "oic_base_url": oic_base_url,
        "service_instance": service_instance,
        "bucket": bucket,
        "prefix": run_prefix,
        "integrations": {"total": 0, "succeeded": 0, "failed": 0, "errors": []},
        "connections": {"total": 0, "backed_up": False},
        "lookups": {"total": 0, "backed_up": False},
        "packages": {"total": 0, "backed_up": False},
    }

    # ── 1. Integrations ──────────────────────────────────────────────────────
    try:
        integrations = list_integrations(
            session, oic_base_url, service_instance, include_inactive
        )
        summary["integrations"]["total"] = len(integrations)

        for intg in integrations:
            intg_id = intg.get("id") or f"{intg['code']}|{intg['version']}"
            safe_name = intg_id.replace("|", "_").replace("/", "_")
            object_name = f"{run_prefix}/integrations/{safe_name}.iar"
            try:
                iar_bytes = export_integration(
                    session, oic_base_url, service_instance, intg_id
                )
                upload_object(os_client, namespace, bucket, object_name, iar_bytes)
                summary["integrations"]["succeeded"] += 1
                logger.info(f"Backed up integration: {intg_id}")
            except Exception as e:
                err_detail = {"integration": intg_id, "error": str(e)}
                summary["integrations"]["errors"].append(err_detail)
                summary["integrations"]["failed"] += 1
                logger.error(f"Failed to back up integration {intg_id}: {e}")

    except Exception as e:
        logger.error(f"Failed to list integrations: {e}\n{traceback.format_exc()}")
        summary["integrations"]["errors"].append({"error": f"List failed: {e}"})
        send_notification(
            topic_ocid,
            "OIC Backup - Integration List Failed",
            f"Could not list integrations from OIC.\n"
            f"OIC URL: {oic_base_url}\nError: {e}",
        )

    # ── 2. Connections (metadata only — no credentials) ──────────────────────
    if backup_connections:
        try:
            connections = list_connections(session, oic_base_url, service_instance)
            summary["connections"]["total"] = len(connections)
            conn_json = json.dumps(connections, indent=2, default=str)
            upload_object(
                os_client, namespace, bucket,
                f"{run_prefix}/connections/connections.json",
                conn_json,
            )
            summary["connections"]["backed_up"] = True
        except Exception as e:
            logger.error(f"Failed to back up connections: {e}")
            summary["connections"]["error"] = str(e)

    # ── 3. Lookups ────────────────────────────────────────────────────────────
    if backup_lookups:
        try:
            lookups = list_lookups(session, oic_base_url, service_instance)
            summary["lookups"]["total"] = len(lookups)
            lookup_json = json.dumps(lookups, indent=2, default=str)
            upload_object(
                os_client, namespace, bucket,
                f"{run_prefix}/lookups/lookups.json",
                lookup_json,
            )
            summary["lookups"]["backed_up"] = True
        except Exception as e:
            logger.error(f"Failed to back up lookups: {e}")
            summary["lookups"]["error"] = str(e)

    # ── 4. Packages ───────────────────────────────────────────────────────────
    if backup_packages:
        try:
            packages = list_packages(session, oic_base_url, service_instance)
            summary["packages"]["total"] = len(packages)
            pkg_json = json.dumps(packages, indent=2, default=str)
            upload_object(
                os_client, namespace, bucket,
                f"{run_prefix}/packages/packages.json",
                pkg_json,
            )
            summary["packages"]["backed_up"] = True
        except Exception as e:
            logger.error(f"Failed to back up packages: {e}")
            summary["packages"]["error"] = str(e)

    # ── 5. Summary manifest ───────────────────────────────────────────────────
    upload_object(
        os_client, namespace, bucket,
        f"{run_prefix}/summary.json",
        json.dumps(summary, indent=2, default=str),
    )

    # ── 6. Notifications ──────────────────────────────────────────────────────
    failed_count = summary["integrations"]["failed"]
    total_count = summary["integrations"]["total"]
    succeeded_count = summary["integrations"]["succeeded"]

    if failed_count > 0:
        send_notification(
            topic_ocid,
            "OIC Backup - Completed with Errors",
            f"OIC metadata backup completed with {failed_count} failure(s).\n"
            f"Integrations: {succeeded_count}/{total_count} succeeded.\n"
            f"Backup location: oci://{bucket}/{run_prefix}/\n"
            f"OIC URL: {oic_base_url}\n"
            f"Failed: "
            + ", ".join(e["integration"] for e in summary["integrations"]["errors"] if "integration" in e),
        )
    else:
        send_notification(
            topic_ocid,
            "OIC Backup - Completed Successfully",
            f"OIC metadata backup completed successfully.\n"
            f"Integrations backed up: {succeeded_count}\n"
            f"Backup location: oci://{bucket}/{run_prefix}/\n"
            f"OIC URL: {oic_base_url}",
        )

    logger.info(
        f"Backup complete. Integrations: {succeeded_count}/{total_count} succeeded, "
        f"{failed_count} failed."
    )
    return summary


if __name__ == "__main__":
    import os
    result = backup_oic_metadata({
        "OIC_BASE_URL": os.environ["OIC_BASE_URL"],
        "OIC_SERVICE_INSTANCE": os.environ["OIC_SERVICE_INSTANCE"],
        "OIC_USERNAME": os.environ.get("OIC_USERNAME", ""),
        "OIC_PASSWORD": os.environ.get("OIC_PASSWORD", ""),
        "OIC_USERNAME_SECRET_OCID": os.environ.get("OIC_USERNAME_SECRET_OCID", ""),
        "OIC_PASSWORD_SECRET_OCID": os.environ.get("OIC_PASSWORD_SECRET_OCID", ""),
        "OBJECT_STORAGE_BUCKET_NAME": os.environ["OBJECT_STORAGE_BUCKET_NAME"],
        "OBJECT_STORAGE_NAMESPACE": os.environ.get("OBJECT_STORAGE_NAMESPACE", ""),
        "BACKUP_PREFIX": os.environ.get("BACKUP_PREFIX", "backups"),
        "INCLUDE_INACTIVE": os.environ.get("INCLUDE_INACTIVE", "false"),
        "BACKUP_CONNECTIONS": os.environ.get("BACKUP_CONNECTIONS", "true"),
        "BACKUP_LOOKUPS": os.environ.get("BACKUP_LOOKUPS", "true"),
        "BACKUP_PACKAGES": os.environ.get("BACKUP_PACKAGES", "true"),
        "NOTIFICATION_TOPIC_OCID": os.environ.get("NOTIFICATION_TOPIC_OCID", ""),
    })
    print(json.dumps(result, indent=2, default=str))
