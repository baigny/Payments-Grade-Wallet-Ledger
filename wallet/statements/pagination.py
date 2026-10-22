"""Untrusted, wallet-bound seek positions; authorization always happens separately."""

import base64
import binascii
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from wallet.core.errors import DomainError
from wallet.core.schemas import MAX_MONEY


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    wallet_id: int = Field(strict=True, ge=1, le=MAX_MONEY)
    entry_id: int = Field(strict=True, ge=1, le=MAX_MONEY)
    created_at: AwareDatetime


def encode(wallet_id, entry):
    position = Position(wallet_id=wallet_id, entry_id=entry["id"], created_at=entry["created_at"])
    return base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")


def decode(value, wallet_id):
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        position = Position.model_validate_json(raw)
        if position.wallet_id != wallet_id:
            raise ValueError("Wrong wallet")
        return position
    except (ValueError, binascii.Error, ValidationError) as exc:
        raise DomainError("INVALID_CURSOR", "Cursor is invalid for this wallet", 422) from exc
