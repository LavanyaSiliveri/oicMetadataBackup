"""
func.py — OCI Function entry point.

Reads SECRET_OCID from the function application config, loads the JSON
config from OCI Vault, then runs whichever backup modules are enabled
by the flags in the Vault config:

  BACKUP_OIC  : "true" (default) — exportServiceInstanceArchive
  BACKUP_VBCS : "true"           — VBCS application archives + optional BO data
  BACKUP_OPA  : "true"           — OPA process + decision model applications
"""

import io
import json
import logging
import traceback

from fdk import response

import oicMetadataBackup
import vbcsBackup
import opaBackup
from shared_utils import get_config_from_vault

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def handler(ctx, data: io.BytesIO = None):
    try:
        secret_ocid = ctx.Config().get("SECRET_OCID", "").strip()
        if not secret_ocid:
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "SECRET_OCID is not configured."}),
                headers={"Content-Type": "application/json"},
                status_code=400,
            )

        # Load config once — all modules share the same Vault secret
        config = get_config_from_vault(secret_ocid)

        backup_oic  = config.get("BACKUP_OIC",  "true").lower()  == "true"
        backup_vbcs = config.get("BACKUP_VBCS", "false").lower() == "true"
        backup_opa  = config.get("BACKUP_OPA",  "false").lower() == "true"

        results = {}

        if backup_oic:
            logger.info("--- Starting OIC backup ---")
            results["oic"] = oicMetadataBackup.run_backup(secret_ocid)

        if backup_vbcs:
            logger.info("--- Starting VBCS backup ---")
            results["vbcs"] = vbcsBackup.run_backup(secret_ocid)

        if backup_opa:
            logger.info("--- Starting OPA backup ---")
            results["opa"] = opaBackup.run_backup(secret_ocid)

        if not results:
            results["warning"] = (
                "No backup modules were enabled. "
                "Set BACKUP_OIC, BACKUP_VBCS, or BACKUP_OPA to 'true' in the Vault secret."
            )

        logger.info("All enabled backups complete.")
        return response.Response(
            ctx,
            response_data=json.dumps(results),
            headers={"Content-Type": "application/json"},
        )

    except Exception as ex:
        logger.error(f"Unexpected error: {ex}\n{traceback.format_exc()}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": f"An error occurred: {str(ex)}"}),
            headers={"Content-Type": "application/json"},
            status_code=500,
        )
