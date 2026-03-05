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

        # Merge function config with any JSON body overrides
        # Function config vars are the primary configuration source;
        # a JSON body (if provided) can override individual keys for ad-hoc runs.
        cfg = dict(ctx.Config())

        if data:
            try:
                body = json.loads(data.getvalue())
                if isinstance(body, dict):
                    cfg.update(body)
            except (ValueError, Exception):
                pass

        summary = oicMetadataBackup.backup_oic_metadata(cfg)

        return response.Response(
            ctx,
            response_data=json.dumps(summary, default=str),
            headers={"Content-Type": "application/json"},
        )

    except ValueError as ve:
        logging.getLogger().error(f"Configuration error: {ve}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": str(ve)}),
            headers={"Content-Type": "application/json"},
            status_code=400,
        )

    except Exception as ex:
        logging.getLogger().error(f"Unexpected error: {ex}\n{traceback.format_exc()}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": f"An error occurred: {str(ex)}"}),
            headers={"Content-Type": "application/json"},
            status_code=500,
        )
