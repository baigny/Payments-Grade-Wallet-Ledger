from typing import Annotated

from fastapi import Path

from wallet.core.schemas import MAX_MONEY

WRITE_RESPONSES = {
    201: {
        "headers": {
            "Idempotent-Replay": {
                "description": "false for the initial response; true for a saved response",
                "schema": {"type": "string", "enum": ["true", "false"]},
            }
        }
    }
}

ResourceID = Annotated[int, Path(ge=1, le=MAX_MONEY)]
