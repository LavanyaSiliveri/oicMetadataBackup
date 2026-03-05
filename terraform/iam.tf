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
#   - read secret-bundles    — load the config JSON from OCI Vault
#   - read integration-instances — check OIC lifecycle state before backup
#   - use ons-topics         — publish success/failure notifications
#
# NOTE: Object Storage write permission is NOT required here.
# OIC writes the archive directly to the bucket using the Swift credentials
# stored inside the Vault secret (SWIFT_USER / SWIFT_PASSWORD / SWIFT_URL).

resource "oci_identity_policy" "fn_policy" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-fn-policy"
  description    = "Allows oicMetadataBackup function to read Vault secrets, check OIC status, and publish notifications."

  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to read secret-bundles in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to read integration-instances in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.fn_dg.name} to use ons-topics in compartment id ${var.compartment_ocid}",
  ]
}
