import io
import json
import logging
import traceback
from fdk import response
import oicMetadataBackup

# Setup logging
logging.basicConfig(level=logging.INFO)


def handler(ctx, data: io.BytesIO = None):
    try:
        logging.getLogger().info("Invoking oicMetadataBackup function")

        secret_ocid = ctx.Config().get("SECRET_OCID", "").strip()
        if not secret_ocid:
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "SECRET_OCID is not configured."}),
                headers={"Content-Type": "application/json"},
                status_code=400,
            )

        result = oicMetadataBackup.run_backup(secret_ocid)

        return response.Response(
            ctx,
            response_data=json.dumps(result),
            headers={"Content-Type": "application/json"},
        )

    except Exception as ex:
        logging.getLogger().error(f"Unexpected error: {ex}\n{traceback.format_exc()}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": f"An error occurred: {str(ex)}"}),
            headers={"Content-Type": "application/json"},
            status_code=500,
        )
