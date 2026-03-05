# ─── Dynamic Group ─────────────────────────────────────────────────────────────
# Dynamic groups are always created at the tenancy level, not compartment level.

resource "oci_identity_dynamic_group" "fn_dg" {
  compartment_id = var.tenancy_ocid
  name           = "${var.prefix}-fn-dynamic-group"
  description    = "Allows OCI Functions in compartment ${var.compartment_ocid} to use Resource Principal auth."
  matching_rule  = "resource.type = 'fnfunc' AND resource.compartment.id = '${var.compartment_ocid}'"
}

# ─── Function IAM Policy ───────────────────────────────────────────────────────
# Grants the Function permission to:
#
#   read secret-bundles        — load the JSON config from OCI Vault
#   read integration-instances — check OIC lifecycle state before backup (OIC module)
#   manage objects             — upload VBCS archives, OPA .expx files, and BO CSVs
#                                directly from the function using the OCI SDK
#                                (OIC writes its own archive via Swift credentials
#                                stored in the Vault secret — no SDK call needed for OIC)
#   use ons-topics             — publish success/failure notifications

resource "oci_identity_policy" "fn_policy" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-fn-policy"
  description    = "Allows oicMetadataBackup function to read Vault, check OIC status, write backups to Object Storage, and publish notifications."

  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to read secret-bundles in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to read integration-instances in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to manage objects in compartment id ${var.compartment_ocid} where target.bucket.name = '${oci_objectstorage_bucket.backup.name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to use ons-topics in compartment id ${var.compartment_ocid}",
  ]
}
