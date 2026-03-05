# ─── OCI Functions Application ─────────────────────────────────────────────────
# Creates the Functions Application that hosts the oicmetadatabackup function.
#
# NOTE: Terraform provisions the Application and its config only.
# The function image must be deployed separately using the fn CLI after apply:
#
#   cd /path/to/oicMetadataBackup
#   fn use context <your-context>
#   fn deploy --app <function_app_name>

resource "oci_functions_application" "this" {
  compartment_id = var.compartment_ocid
  display_name   = var.function_app_name
  subnet_ids     = var.subnet_ids

  # Application-level config is inherited by all functions within this app.
  # Sensitive values (passwords) should be stored in OCI Vault and referenced
  # via the *_SECRET_OCID keys rather than stored here in plain text.
  config = {
    OIC_BASE_URL               = var.oic_base_url
    OIC_SERVICE_INSTANCE       = var.oic_service_instance
    OIC_USERNAME               = var.oic_username
    OIC_PASSWORD               = var.oic_password
    OIC_USERNAME_SECRET_OCID   = var.oic_username_secret_ocid
    OIC_PASSWORD_SECRET_OCID   = var.oic_password_secret_ocid
    OBJECT_STORAGE_BUCKET_NAME = var.bucket_name
    OBJECT_STORAGE_NAMESPACE   = data.oci_objectstorage_namespace.this.namespace
    BACKUP_PREFIX              = var.backup_prefix
    INCLUDE_INACTIVE           = tostring(var.include_inactive)
    BACKUP_CONNECTIONS         = tostring(var.backup_connections)
    BACKUP_LOOKUPS             = tostring(var.backup_lookups)
    BACKUP_PACKAGES            = tostring(var.backup_packages)
    NOTIFICATION_TOPIC_OCID    = oci_ons_notification_topic.this.id
  }
}
