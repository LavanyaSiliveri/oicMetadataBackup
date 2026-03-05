# oicMetadataBackup — OCI Function

An OCI Scheduled Function that takes periodic metadata backups of an Oracle Integration Cloud (OIC) instance and its associated services. A single function handles three backup modules, all controlled by flags in one OCI Vault secret:

| Module | What it backs up | API |
|---|---|---|
| **OIC** | Full design-time archive written directly to Object Storage via Swift | `exportServiceInstanceArchive` |
| **VBCS** | Every Visual Builder application as a zip, plus optional Business Object data as CSV | VBCS Builder Resources API |
| **OPA** | Every Process Automation application and Decision Model as `.expx` | OPA Design REST API |

---

## Architecture

```
OCI Scheduled Functions (cron)
          │
          ▼
   OCI Function (oicmetadatabackup)
          │
          ├── shared_utils.py — Vault, OAuth2, Object Storage, ONS helpers
          │
          ├── oicMetadataBackup.py  (BACKUP_OIC=true)
          │     Check OIC instance ACTIVE → OAuth2 token → POST exportServiceInstanceArchive
          │     OIC writes archive directly to Object Storage via Swift
          │     Poll job until COMPLETED/FAILED → notify
          │
          ├── vbcsBackup.py  (BACKUP_VBCS=true)
          │     OAuth2 token → list apps → export each app as .zip
          │     Optionally list + export Business Object data as CSV
          │     Upload to Object Storage via OCI SDK → notify on failure
          │
          └── opaBackup.py  (BACKUP_OPA=true)
                OAuth2 token → list process apps → export each as .expx
                List decision (DMN) apps → export each as .expx
                Upload to Object Storage via OCI SDK → notify on failure
```

---

## Object Storage layout

```
{bucket}/
├── (OIC writes its archive here directly — filename set by OIC job)
├── vbcs-backup/
│   └── {YYYY-MM-DD_HH-MM-SS}/
│       ├── MyApp_1_0.zip
│       └── MyApp/
│           └── data/
│               ├── Employee.csv
│               └── Department.csv
└── opa-backup/
    └── {YYYY-MM-DD_HH-MM-SS}/
        ├── process/
        │   └── OnboardingProcess_1.0.expx
        └── decisions/
            └── CreditDecision_1.0.expx
```

---

## Project structure

```
oicMetadataBackup/
├── func.py                # FDK handler — reads SECRET_OCID, runs enabled modules
├── shared_utils.py        # Common: Vault loader, OAuth2, Object Storage, ONS
├── oicMetadataBackup.py   # OIC exportServiceInstanceArchive + poll
├── vbcsBackup.py          # VBCS app archive + Business Object CSV export
├── opaBackup.py           # OPA process + DMN application .expx export
├── func.yaml              # Python 3.11, 512 MB, 300s timeout
├── requirements.txt       # fdk, oci, requests
└── terraform/
    ├── provider.tf
    ├── variables.tf
    ├── iam.tf             # Dynamic Group + policy (Vault, OIC, Object Storage, ONS)
    ├── object_storage.tf  # Backup bucket
    ├── notifications.tf   # ONS topic + email subscription
    ├── functions.tf       # Functions Application
    └── outputs.tf
```

---

## Prerequisites

- OIC instance with a **Confidential Application** registered in IDCS/IAM
- OCI IAM user with an **Auth Token** (for OIC Swift storage — OIC module only)
- OCI Vault already created
- `fn` CLI installed and configured
- Docker installed and running

---

## Vault Secret JSON Schema

All configuration lives in a single JSON secret stored in OCI Vault. The `SECRET_OCID` of this secret is the only value the function needs at runtime.

```json
{
  "OIC_CLIENT_ID":       "<OAUTH_APP_CLIENT_ID>",
  "OIC_CLIENT_SECRET":   "<OAUTH_APP_CLIENT_SECRET>",
  "OIC_IDCS_TOKEN_URL":  "https://idcs-xxxxxxxx.identity.oraclecloud.com/oauth2/v1/token",
  "OIC_SCOPE":           "https://<OIC_ID>.integration.<REGION>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all",
  "OIC_INSTANCE_NAME":   "<OIC_INSTANCE_NAME>",
  "OIC_INSTANCE_OCID":   "ocid1.integrationinstance.oc1...",
  "OIC_API_HOST":        "design.integration.<REGION>.ocp.oraclecloud.com",
  "SWIFT_URL":           "https://swiftobjectstorage.<REGION>.oraclecloud.com/v1/<NAMESPACE>/<BUCKET>",
  "SWIFT_USER":          "<TENANCY>/<USERNAME>",
  "SWIFT_PASSWORD":      "<AUTH_TOKEN>",

  "OBJ_STORAGE_NAMESPACE": "<TENANCY_NAMESPACE>",
  "OBJ_STORAGE_BUCKET":    "<BUCKET_NAME>",

  "VBCS_HOST":  "design.integration.<REGION>.ocp.oraclecloud.com",
  "VBCS_SCOPE": "https://<OIC_ID>.integration.<REGION>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all",

  "OPA_HOST":  "<OPA_INSTANCE_HOST>",
  "OPA_SCOPE": "<OPA_OAUTH_SCOPE>",

  "ONS_TOPIC_OCID": "ocid1.onstopic.oc1...",

  "BACKUP_OIC":           "true",
  "BACKUP_VBCS":          "true",
  "BACKUP_OPA":           "true",
  "BACKUP_VBCS_DATA":     "false",
  "EXPORT_DECISION_APPS": "true"
}
```

### Credential fallback

`VBCS_CLIENT_ID`, `VBCS_CLIENT_SECRET`, `VBCS_IDCS_TOKEN_URL`, `OPA_CLIENT_ID`, `OPA_CLIENT_SECRET`, and `OPA_IDCS_TOKEN_URL` all fall back to the `OIC_*` equivalents when absent. If all three services share one IDCS confidential app, you only need the `OIC_*` credential keys plus service-specific `SCOPE` and `HOST` values.

---

## Terraform — Automated Infrastructure Provisioning

### What it provisions

| File | Resources |
|---|---|
| `iam.tf` | Dynamic Group + IAM policy (Vault, OIC instance check, Object Storage write, ONS) |
| `object_storage.tf` | Private OCI Object Storage bucket |
| `notifications.tf` | ONS Notification Topic + Email subscription |
| `functions.tf` | Functions Application with `SECRET_OCID` config |

### Usage

**1. Create `terraform.tfvars`** (excluded from git):

```hcl
tenancy_ocid       = "ocid1.tenancy.oc1..."
user_ocid          = "ocid1.user.oc1..."
fingerprint        = "aa:bb:cc:..."
private_key_path   = "~/.oci/oci_api_key.pem"
region             = "ap-sydney-1"
compartment_ocid   = "ocid1.compartment.oc1..."
subnet_ids         = ["ocid1.subnet.oc1..."]
secret_ocid        = "ocid1.vaultsecret.oc1..."
notification_email = "you@example.com"
```

**2. Apply and deploy:**

```bash
cd terraform && terraform init && terraform apply
cd .. && fn deploy --app OICBackupFuncApp
```

**3. Test:**

```bash
echo '{}' | fn invoke OICBackupFuncApp oicmetadatabackup
```

---

## Manual IAM Setup (without Terraform)

### Dynamic Group

```
resource.type = 'fnfunc' AND resource.compartment.id = '<compartment-ocid>'
```

### IAM Policies

```
Allow dynamic-group <dg-name> to read secret-bundles in compartment <compartment>
Allow dynamic-group <dg-name> to read integration-instances in compartment <compartment>
Allow dynamic-group <dg-name> to manage objects in compartment <compartment> where target.bucket.name = '<bucket>'
Allow dynamic-group <dg-name> to use ons-topics in compartment <compartment>
```

---

## Scheduling

1. OCI Console → **Functions → Applications → OICBackupFuncApp**
2. Click `oicmetadatabackup` → **Triggers → Add Trigger**
3. Select **Scheduled** → set cron expression (e.g. `0 21 * * *` for 9 PM UTC daily)

---

## Response

```json
{
  "oic":  { "status": "COMPLETED", "jobId": "6d3446ed-xxxx" },
  "vbcs": { "status": "COMPLETED", "succeeded": 4, "failed": 0, "details": [...] },
  "opa":  { "status": "COMPLETED", "succeeded": 6, "failed": 0, "details": [...] }
}
```

### Status values

| Status | Meaning |
|---|---|
| `COMPLETED` | All artifacts backed up successfully |
| `PARTIAL` | Some artifacts failed — see `details` and check ONS notification |
| `FAILED` | Auth or list call failed before any export ran |
| `SKIPPED` | OIC instance was INACTIVE (OIC module only) |
| `TIMEOUT` | OIC export job did not complete within 270s (OIC module only) |

---

## Notification triggers

| Module | Subject |
|---|---|
| OIC — success | `OIC Backup Completed Successfully` |
| OIC — job failed | `OIC Backup Failed` |
| OIC — timeout | `OIC Backup Failed - Timeout` |
| OIC — instance inactive | `OIC Backup Skipped - Instance Not Active` |
| VBCS — auth error | `VBCS Backup Failed - Auth Error` |
| VBCS — partial | `VBCS Backup - Partial Failure` |
| OPA — auth error | `OPA Backup Failed - Auth Error` |
| OPA — partial | `OPA Backup - Partial Failure` |

---

## Authentication

All OCI SDK calls (Vault, Integration Instance, Object Storage, ONS) use **Resource Principal** — no credentials embedded in the function. OIC, VBCS, and OPA authenticate via **OAuth2 client credentials** obtained at runtime from IDCS using values stored in the Vault secret.

---

## Tuning

| Parameter | File | Default | Notes |
|---|---|---|---|
| `POLL_INTERVAL_SECONDS` | `oicMetadataBackup.py` | 15s | OIC job poll frequency |
| `DEFAULT_TIMEOUT_SECONDS` | `oicMetadataBackup.py` | 270s | Must be < function timeout (300s) |
| `memory` | `func.yaml` | 512 MB | Increase if handling very large VBCS/OPA apps |
| `timeout` | `func.yaml` | 300s | Increase if all three modules run on a large instance |
| `BACKUP_VBCS_DATA` | Vault secret | `false` | Enable with caution — large BOs extend runtime significantly |
