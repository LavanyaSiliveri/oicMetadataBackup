# ─── ONS Notification Topic ────────────────────────────────────────────────────

resource "oci_ons_notification_topic" "this" {
  compartment_id = var.compartment_ocid
  name           = "${var.prefix}-backup-notifications"
  description    = "Receives success and failure alerts from the oicMetadataBackup OCI Function."
}

# ─── Email Subscription ────────────────────────────────────────────────────────
# OCI will send a confirmation email to the address below.
# The subscriber MUST click the confirmation link before alerts are delivered.

resource "oci_ons_subscription" "email" {
  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.this.id
  protocol       = "EMAIL"
  endpoint       = var.notification_email
}
