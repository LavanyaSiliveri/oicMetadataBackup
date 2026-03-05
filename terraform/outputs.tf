# ─── Functions Application ────────────────────────────────────────────────────

output "functions_application_ocid" {
  description = "OCID of the provisioned OCI Functions Application."
  value       = oci_functions_application.this.id
}

output "functions_application_name" {
  description = "Display name of the OCI Functions Application."
  value       = oci_functions_application.this.display_name
}

# ─── Object Storage ───────────────────────────────────────────────────────────

output "backup_bucket_name" {
  description = "Name of the OCI Object Storage bucket where OIC will write archives."
  value       = oci_objectstorage_bucket.backup.name
}

output "object_storage_namespace" {
  description = "OCI Object Storage namespace — use this in the SWIFT_URL inside your Vault secret."
  value       = data.oci_objectstorage_namespace.this.namespace
}

output "swift_url" {
  description = "Swift-compatible URL for the backup bucket. Use this as SWIFT_URL in the Vault secret config."
  value       = "https://swiftobjectstorage.${var.region}.oraclecloud.com/v1/${data.oci_objectstorage_namespace.this.namespace}/${oci_objectstorage_bucket.backup.name}"
}

# ─── Notifications ────────────────────────────────────────────────────────────

output "notification_topic_ocid" {
  description = "OCID of the ONS notification topic — use this as ONS_TOPIC_OCID in the Vault secret config."
  value       = oci_ons_notification_topic.this.id
}

# ─── IAM ──────────────────────────────────────────────────────────────────────

output "dynamic_group_name" {
  description = "Name of the Dynamic Group created for the OCI Function."
  value       = oci_identity_dynamic_group.fn_dg.name
}

# ─── Invocation ───────────────────────────────────────────────────────────────

output "fn_deploy_command" {
  description = "Command to deploy the function after terraform apply."
  value       = "cd /path/to/oicMetadataBackup && fn deploy --app ${oci_functions_application.this.display_name}"
}

output "fn_invoke_command" {
  description = "Command to manually trigger a backup."
  value       = "echo '{}' | fn invoke ${oci_functions_application.this.display_name} oicmetadatabackup"
}

# ─── Post-apply Reminder ──────────────────────────────────────────────────────

output "next_steps" {
  description = "Actions required after terraform apply."
  value       = <<-EOT
    1. Confirm the subscription email sent to ${var.notification_email}.
    2. Create the Vault secret JSON (see README) using these values:
         SWIFT_URL        = https://swiftobjectstorage.${var.region}.oraclecloud.com/v1/${data.oci_objectstorage_namespace.this.namespace}/${oci_objectstorage_bucket.backup.name}
         ONS_TOPIC_OCID   = ${oci_ons_notification_topic.this.id}
    3. Store the secret OCID in terraform.tfvars as secret_ocid.
    4. Deploy the function:
         cd /path/to/oicMetadataBackup
         fn use context <your-fn-context>
         fn deploy --app ${oci_functions_application.this.display_name}
    5. Test:
         echo '{}' | fn invoke ${oci_functions_application.this.display_name} oicmetadatabackup
    6. Schedule: OCI Console -> Functions -> Application -> oicmetadatabackup -> Triggers -> Add Scheduled Trigger
  EOT
}
