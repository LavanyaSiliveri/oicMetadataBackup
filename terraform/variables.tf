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

# ─── OIC Configuration ────────────────────────────────────────────────────────

variable "oic_base_url" {
  type        = string
  description = "Base URL of the OIC instance, e.g. https://myoic.integration.ocp.oraclecloud.com"
}

variable "oic_service_instance" {
  type        = string
  description = "OIC service instance name (visible on the OIC About page under 'Service instance')."
}

variable "oic_username" {
  type        = string
  description = "OIC username for basic authentication. Leave empty if using Vault secrets."
  default     = ""
  sensitive   = true
}

variable "oic_password" {
  type        = string
  description = "OIC password for basic authentication. Leave empty if using Vault secrets."
  default     = ""
  sensitive   = true
}

variable "oic_username_secret_ocid" {
  type        = string
  description = "OCID of the OCI Vault secret that holds the OIC username. Takes priority over oic_username."
  default     = ""
}

variable "oic_password_secret_ocid" {
  type        = string
  description = "OCID of the OCI Vault secret that holds the OIC password. Takes priority over oic_password."
  default     = ""
}

# ─── Object Storage ───────────────────────────────────────────────────────────

variable "bucket_name" {
  type        = string
  description = "Name of the OCI Object Storage bucket for storing backups."
  default     = "oic-metadata-backups"
}

variable "bucket_access_type" {
  type        = string
  description = "Access type for the backup bucket. Use NoPublicAccess for private backups."
  default     = "NoPublicAccess"
}

# ─── Backup Behaviour ─────────────────────────────────────────────────────────

variable "backup_prefix" {
  type        = string
  description = "Folder prefix within the bucket for all backup runs (e.g. 'backups')."
  default     = "backups"
}

variable "include_inactive" {
  type        = bool
  description = "When true, backs up non-ACTIVATED integrations (CONFIGURED, DRAFT) as well."
  default     = false
}

variable "backup_connections" {
  type        = bool
  description = "When true, exports connection metadata (no credentials) as JSON."
  default     = true
}

variable "backup_lookups" {
  type        = bool
  description = "When true, exports lookup table definitions as JSON."
  default     = true
}

variable "backup_packages" {
  type        = bool
  description = "When true, exports package list as JSON."
  default     = true
}

# ─── Notifications ─────────────────────────────────────────────────────────────

variable "notification_email" {
  type        = string
  description = "Email address for backup success/failure notifications. Must confirm OCI subscription email."
}
