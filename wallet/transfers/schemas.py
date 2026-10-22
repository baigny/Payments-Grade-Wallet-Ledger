from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wallet.core.schemas import AccountID, Amount, InputModel

TRANSFER_EXAMPLES = {
    "peer": {
        "summary": "Send to another wallet",
        "value": {
            "type": "peer",
            "source_account_id": 4,
            "destination_account_id": 5,
            "amount": 1000,
        },
    },
    "top_up": {
        "summary": "Admin simulates a top-up",
        "value": {"type": "top_up", "destination_account_id": 4, "amount": 25000},
    },
    "withdrawal": {
        "summary": "Withdraw from an owned wallet",
        "value": {"type": "withdrawal", "source_account_id": 4, "amount": 750},
    },
    "fee": {
        "summary": "Admin charges a fee",
        "value": {"type": "fee", "source_account_id": 4, "amount": 100},
    },
}


class TransferCreate(InputModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "peer",
                "source_account_id": 4,
                "destination_account_id": 5,
                "amount": 1000,
                "note": "Shared lunch",
            }
        }
    )
    type: Literal["top_up", "peer", "withdrawal", "fee"]
    source_account_id: AccountID | None = None
    destination_account_id: AccountID | None = None
    amount: Amount
    note: str = Field(default="", max_length=500)


class TransferResponse(BaseModel):
    id: int
    source_account_id: int
    destination_account_id: int
    type: str
    status: Literal["posted", "held", "rejected"]
    amount: int
    currency: Literal["INR"]
    reversal_of: int | None
    note: str
    created_at: datetime
