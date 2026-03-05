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
  description = "Name of the OCI Object Storage bucket where backups are stored."
  value       = oci_objectstorage_bucket.backup.name
}

output "object_storage_namespace" {
  description = "OCI Object Storage namespace for this tenancy."
  value       = data.oci_objectstorage_namespace.this.namespace
}

# ─── Notifications ────────────────────────────────────────────────────────────

output "notification_topic_ocid" {
  description = "OCID of the ONS notification topic. Also set as NOTIFICATION_TOPIC_OCID in the function app config."
  value       = oci_ons_notification_topic.this.id
}

# ─── IAM ──────────────────────────────────────────────────────────────────────

output "dynamic_group_name" {
  description = "Name of the Dynamic Group created for the OCI Function."
  value       = oci_identity_dynamic_group.fn_dg.name
}

# ─── Ready-to-use Invocation Commands ────────────────────────────────────────

output "fn_deploy_command" {
  description = "Command to deploy the function after terraform apply."
  value       = "cd /path/to/oicMetadataBackup && fn deploy --app ${oci_functions_application.this.display_name}"
}

output "fn_invoke_command" {
  description = "Command to manually invoke the backup function."
  value       = "echo '{}' | fn invoke ${oci_functions_application.this.display_name} oicmetadatabackup"
}

output "fn_invoke_command_with_override" {
  description = "Example invocation overriding a single config value (e.g. run a one-off backup to a different prefix)."
  value = jsonencode({
    BACKUP_PREFIX = "manual-backup"
  })
}

# ─── Post-apply Reminder ──────────────────────────────────────────────────────

output "next_steps" {
  description = "Actions required after terraform apply."
  value       = <<-EOT
    1. Confirm the subscription email sent to ${var.notification_email}.
    2. Deploy the function:
         cd /path/to/oicMetadataBackup
         fn use context <your-fn-context>
         fn deploy --app ${oci_functions_application.this.display_name}
    3. Test the function:
         echo '{}' | fn invoke ${oci_functions_application.this.display_name} oicmetadatabackup
    4. Schedule the function (see README — Scheduling section).
  EOT
}
