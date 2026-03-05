# ─── OCI Provider Authentication ──────────────────────────────────────────────

variable "tenancy_ocid" {
  type        = string
  description = "OCID of the OCI tenancy."
}

variable "user_ocid" {
  type        = string
  description = "OCID of the OCI user used by Terraform."
}

variable "fingerprint" {
  type        = string
  description = "Fingerprint of the API key associated with the OCI user."
}

variable "private_key_path" {
  type        = string
  description = "Local path to the OCI API private key file (.pem)."
  default     = "~/.oci/oci_api_key.pem"
}

variable "region" {
  type        = string
  description = "OCI region identifier (e.g. ap-sydney-1)."
}

# ─── Resource Placement ────────────────────────────────────────────────────────

variable "compartment_ocid" {
  type        = string
  description = "OCID of the compartment where all resources will be created."
}

variable "prefix" {
  type        = string
  description = "Short prefix applied to all resource names to avoid collisions."
  default     = "oicbackup"
}

# ─── OCI Functions ─────────────────────────────────────────────────────────────

variable "function_app_name" {
  type        = string
  description = "Display name for the OCI Functions Application."
  default     = "OICBackupFuncApp"
}

variable "subnet_ids" {
  type        = list(string)
  description = "List of subnet OCIDs for the Functions Application. At least one required."
}

# ─── Vault Config Secret ──────────────────────────────────────────────────────
# The function reads a single JSON secret from OCI Vault that contains all
# OIC credentials and storage configuration (see README for the secret schema).

variable "secret_ocid" {
  type        = string
  description = "OCID of the OCI Vault secret holding the OIC backup config JSON."
}

# ─── Notifications ─────────────────────────────────────────────────────────────
# The ONS topic OCID is embedded inside the Vault secret (ONS_TOPIC_OCID key).
# The email variable below is only used by Terraform to provision the topic
# and its subscription; it must match what is placed in the Vault secret.

variable "notification_email" {
  type        = string
  description = "Email address for backup success/failure notifications. Must confirm the OCI subscription email."
}
