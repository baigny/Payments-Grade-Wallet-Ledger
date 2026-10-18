from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_MONEY = 2**63 - 1
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
AccountID = Annotated[int, Field(strict=True, ge=1, le=MAX_MONEY)]
Amount = Annotated[int, Field(strict=True, gt=0, le=MAX_MONEY)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class UserCreate(InputModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Nila", "email": "nila@example.test", "phone": "+919123456780"}
        }
    )
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: str
    kyc_status: str


class UserCreated(UserResponse):
    token: str = Field(
        description=(
            "Store securely. Authentication stores a hash; the original creation response, "
            "including this token, is retained for administrator-scoped idempotent replay."
        )
    )


class WalletCreate(InputModel):
    model_config = ConfigDict(json_schema_extra={"example": {"name": "Main"}})
    name: str = Field(min_length=1, max_length=80)


class WalletResponse(BaseModel):
    id: int
    user_id: int
    name: str
    currency: Literal["INR"]
    balance: int


class WalletList(BaseModel):
    items: list[WalletResponse]


class StatementEntry(BaseModel):
    id: int
    transfer_id: int
    direction: Literal["debit", "credit"]
    amount: int
    type: str
    note: str
    created_at: datetime


class StatementResponse(BaseModel):
    wallet_id: int
    currency: Literal["INR"]
    limit: int
    offset: int
    items: list[StatementEntry]


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
    status: Literal["posted"]
    amount: int
    currency: Literal["INR"]
    reversal_of: int | None
    note: str
    created_at: datetime
