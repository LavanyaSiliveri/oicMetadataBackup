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

  # The only config the function needs at runtime is the Vault secret OCID.
  # All OIC credentials, storage details, and the ONS topic OCID are read
  # from the JSON stored inside that secret.
  config = {
    SECRET_OCID = var.secret_ocid
  }
}
