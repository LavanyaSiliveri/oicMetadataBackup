# ─── Object Storage Namespace (data source) ────────────────────────────────────

data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_ocid
}

# ─── Backup Bucket ─────────────────────────────────────────────────────────────
# Stores all OIC metadata backup runs under:
#   {backup_prefix}/{YYYY-MM-DD_HH-MM-SS}/integrations/*.iar
#   {backup_prefix}/{YYYY-MM-DD_HH-MM-SS}/connections/connections.json
#   {backup_prefix}/{YYYY-MM-DD_HH-MM-SS}/lookups/lookups.json
#   {backup_prefix}/{YYYY-MM-DD_HH-MM-SS}/packages/packages.json
#   {backup_prefix}/{YYYY-MM-DD_HH-MM-SS}/summary.json

resource "oci_objectstorage_bucket" "backup" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = var.bucket_name
  access_type    = var.bucket_access_type

  # Versioning is disabled by default; enable if you want object-level version history.
  versioning = "Disabled"

  freeform_tags = {
    "project"   = "oic-metadata-backup"
    "managed-by" = "terraform"
  }
}
