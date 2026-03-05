# ─── Object Storage Namespace (data source) ────────────────────────────────────

data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_ocid
}

# ─── Backup Bucket ─────────────────────────────────────────────────────────────
# OIC writes the service instance archive directly to this bucket using the
# Swift-compatible endpoint. The function itself never touches the bucket —
# it only triggers the OIC export job and polls for completion.
#
# After apply, use the outputs below to construct the SWIFT_URL for the
# Vault secret config:
#   https://swiftobjectstorage.<region>.oraclecloud.com/v1/<namespace>/<bucket-name>

resource "oci_objectstorage_bucket" "backup" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = "${var.prefix}-oic-metadata-backups"
  access_type    = "NoPublicAccess"
  versioning     = "Disabled"

  freeform_tags = {
    "project"    = "oic-metadata-backup"
    "managed-by" = "terraform"
  }
}
