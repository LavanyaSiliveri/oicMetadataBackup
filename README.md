# oicMetadataBackup — OCI Function

An OCI Scheduled Function that takes periodic metadata backups of an **Oracle Integration Cloud (OIC)** instance. It uses the OIC `exportServiceInstanceArchive` API to export the complete OIC design-time metadata in a single job — OIC writes the archive directly to OCI Object Storage via the Swift endpoint. The function triggers the export, polls for completion, and sends success or failure alerts via OCI Notification Service (ONS).

---

## Advantages

- Keep track of changes made to integrations and connections over time
- Metadata is readily available to clone the existing instance for quick testing without changing the production environment
- Flexibility to access a particular date/time snapshot of integration configuration
- Checks OIC instance status before initiating the backup and notifies admin if the instance is INACTIVE
- Notified on any failure so no backup is silently missed

---

## Architecture

```
OCI Scheduled Functions (cron)
          │
          ▼
   OCI Function (oicmetadatabackup)
          │
          ├── Reads config JSON from OCI Vault secret
          │     (OIC credentials, storage info, ONS topic)
          │
          ├── Checks OIC instance lifecycle state via OCI SDK
          │     → Skips and notifies if INACTIVE
          │
          ├── Obtains OAuth2 token from IDCS (client credentials)
          │
          ├── POST /ic/api/common/v1/exportServiceInstanceArchive
          │     → OIC writes archive DIRECTLY to Object Storage (Swift)
          │     → Function only triggers and polls — no data flows through it
          │
          ├── Polls GET /ic/api/common/v1/exportServiceInstanceArchive/{jobId}
          │     until COMPLETED / FAILED / timeout
          │
          └── Publishes result to ONS → Email notification
```

---

## OCI Services used

| Service | Purpose |
|---|---|
| OCI Functions | Hosts and runs the backup function |
| OCI Vault | Stores all config (OIC credentials, Swift URL, ONS topic OCID) as a single JSON secret |
| OCI Object Storage | Receives the archive written directly by OIC via Swift |
| OCI Notification Service (ONS) | Sends success/failure email alerts |
| OCI Identity (IAM) | Dynamic Group + policies for Resource Principal auth |

---

## Project structure

```
oicMetadataBackup/
├── func.py                  # FDK handler — reads SECRET_OCID, calls run_backup()
├── oicMetadataBackup.py     # Core logic: Vault, OAuth2, export, poll, notify
├── func.yaml                # Python 3.11, 512 MB, 300s timeout
├── requirements.txt         # fdk, oci, requests
└── terraform/               # Infrastructure-as-Code
    ├── provider.tf
    ├── variables.tf
    ├── iam.tf               # Dynamic Group + IAM policy
    ├── object_storage.tf    # Backup bucket (OIC writes here via Swift)
    ├── notifications.tf     # ONS topic + email subscription
    ├── functions.tf         # Functions Application (SECRET_OCID config only)
    └── outputs.tf           # swift_url, topic OCID, deploy/invoke commands
```

---

## Prerequisites

- An OIC instance with a **Confidential Application** (OAuth client) registered in IDCS/IAM with the `ServiceAdministrator` or `ServiceDeveloper` role
- An OCI IAM user (service account) with an **Auth Token** — used as `SWIFT_PASSWORD` in the Vault secret
- OCI Vault already created
- `fn` CLI installed and configured (`fn use context <your-context>`)
- Docker installed and running

---

## Setup

### Step 1 — Create an IAM Service Account and Auth Token

1. In **Identity → Users**, create or select a service account user
2. Go to that user → **Auth Tokens → Generate Token**
3. Note the token value — this is your `SWIFT_PASSWORD`
4. The `SWIFT_USER` format is `<tenancy_name>/<username>`

### Step 2 — Register a Confidential Application in IDCS

1. In the OIC Console → **Settings → OAuth** (or via IDCS/IAM), create a Confidential Application
2. Grant it the OIC scope:
   ```
   https://<OIC_INSTANCE_ID>.integration.<REGION>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all
   ```
3. Note the `Client ID` and `Client Secret`
4. Note the IDCS token URL: `https://idcs-<id>.identity.oraclecloud.com/oauth2/v1/token`

### Step 3 — Create an ONS Topic and Email Subscription

1. Go to **Notifications → Create Topic**
2. Add an **Email subscription** and confirm the subscription email
3. Note the **Topic OCID**

### Step 4 — Store config in OCI Vault as a secret

Create a Vault secret with the following JSON value:

```json
{
  "OIC_CLIENT_ID":       "<OAUTH_APP_CLIENT_ID>",
  "OIC_CLIENT_SECRET":   "<OAUTH_APP_CLIENT_SECRET>",
  "OIC_IDCS_TOKEN_URL":  "https://idcs-xxxxxxxx.identity.oraclecloud.com/oauth2/v1/token",
  "OIC_SCOPE":           "https://<OIC_INSTANCE_ID>.integration.<REGION>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all",
  "OIC_INSTANCE_NAME":   "<OIC_INSTANCE_NAME>",
  "OIC_INSTANCE_OCID":   "<OIC_INSTANCE_OCID>",
  "OIC_API_HOST":        "design.integration.<REGION>.ocp.oraclecloud.com",
  "SWIFT_URL":           "https://swiftobjectstorage.<REGION>.oraclecloud.com/v1/<NAMESPACE>/<BUCKET_NAME>",
  "SWIFT_USER":          "<TENANCY_NAME>/<USERNAME>",
  "SWIFT_PASSWORD":      "<AUTH_TOKEN>",
  "ONS_TOPIC_OCID":      "<ONS_TOPIC_OCID>"
}
```

> The `SWIFT_URL`, `NAMESPACE`, and `ONS_TOPIC_OCID` values are output by `terraform apply` — you can run Terraform first, then create the secret.

Note the **Secret OCID** — this is the only value the function needs at runtime.

### Step 5 — Configure the OIC Instance Storage

In the OIC Console → **Settings → Storage**, configure the storage URL using the same Swift URL. This is required for `exportServiceInstanceArchive` to know where to write.

---

## Terraform — Automated Infrastructure Provisioning

### What it provisions

| File | Resources |
|---|---|
| `iam.tf` | Dynamic Group + IAM policy (read Vault secrets, read integration-instances, use ONS topics) |
| `object_storage.tf` | Private OCI Object Storage bucket for archives |
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

# Optional overrides
# prefix            = "oicbackup"
# function_app_name = "OICBackupFuncApp"
```

**2. Apply:**

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

**3. Note the outputs** — use `swift_url` and `notification_topic_ocid` when creating the Vault secret (Step 4 above).

**4. Deploy the function:**

```bash
cd ..
fn deploy --app OICBackupFuncApp
```

**5. Test:**

```bash
echo '{}' | fn invoke OICBackupFuncApp oicmetadatabackup
```

---

## Manual Setup (without Terraform)

### Dynamic Group

```
resource.type = 'fnfunc' AND resource.compartment.id = '<compartment-ocid>'
```

### IAM Policy

```
Allow dynamic-group <dg-name> to read secret-bundles in compartment <compartment-name>
Allow dynamic-group <dg-name> to read integration-instances in compartment <compartment-name>
Allow dynamic-group <dg-name> to use ons-topics in compartment <compartment-name>
```

---

## Deploy

```bash
cd oicMetadataBackup
fn deploy --app OICBackupFuncApp
```

---

## Invoke

The function takes no input parameters — everything is read from the Vault secret.

```bash
echo '{}' | fn invoke OICBackupFuncApp oicmetadatabackup
```

---

## Scheduling

Use **OCI Scheduled Functions** to run the backup on a cron schedule:

1. OCI Console → **Developer Services → Functions → Applications → OICBackupFuncApp**
2. Click the `oicmetadatabackup` function → **Triggers → Add Trigger**
3. Select **Scheduled** trigger type
4. Set the cron expression, e.g. `0 21 * * *` to run daily at 9 PM UTC

---

## Response

```json
{ "status": "COMPLETED", "jobId": "6d3446ed-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
```

| Status | Meaning |
|---|---|
| `COMPLETED` | Archive written successfully to Object Storage |
| `FAILED` | OIC reported the export job failed |
| `TIMEOUT` | Job did not complete within 270 seconds |
| `SKIPPED` | OIC instance was INACTIVE — backup not initiated |

---

## Notification triggers

| Scenario | Subject |
|---|---|
| Export completed successfully | `OIC Backup Completed Successfully` |
| Export job failed | `OIC Backup Failed` |
| Job timed out | `OIC Backup Failed - Timeout` |
| OIC instance not ACTIVE | `OIC Backup Skipped - Instance Not Active` |

---

## Authentication

The function uses **Resource Principal** for all OCI SDK calls (Vault, OCI Integration client, ONS). OIC itself is authenticated via **OAuth2 client credentials** — the IDCS token is obtained at runtime using credentials stored in the Vault secret.

---

## Configuration

Key parameters in [oicMetadataBackup.py](oicMetadataBackup.py):

| Parameter | Default | Description |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `15` | How often to poll the export job status |
| `DEFAULT_TIMEOUT_SECONDS` | `270` | Max polling time (fits inside the 300s function timeout) |
