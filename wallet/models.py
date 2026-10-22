from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    next_cursor: str | None = None


class StatementSummary(BaseModel):
    wallet_id: int
    currency: Literal["INR"]
    from_date: date
    to_date: date
    opening_balance: int
    credit_total: int
    debit_total: int
    closing_balance: int
    transfer_count: int


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


class ReconciliationRun(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    status: Literal["running", "passed", "failed", "error"]
    finding_count: int
    problem_count: int
    account_count: int
    transfer_count: int
    entry_count: int
    trigger_type: Literal["manual", "scheduled", "cli"]
    scheduled_for: date | None
    error_message: str | None


class ReconciliationFinding(BaseModel):
    id: int
    run_id: int
    check_name: str
    entity_type: str
    entity_id: int | None
    expected: dict
    actual: dict
    created_at: datetime


class ReconciliationFindings(BaseModel):
    items: list[ReconciliationFinding]
    limit: int
    offset: int


class RiskRuleUpdate(InputModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "threshold": 100000,
                "window_seconds": None,
                "action": "hold",
                "enabled": True,
            }
        }
    )
    threshold: Amount
    window_seconds: Annotated[int, Field(strict=True, ge=1, le=2592000)] | None = None
    action: Literal["hold", "flag"]
    enabled: bool = Field(strict=True)


class RiskRule(RiskRuleUpdate):
    id: int
    name: str
    rule_type: str


class RiskRules(BaseModel):
    items: list[RiskRule]


class ReviewDecision(InputModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"reason": "Verified with wallet owner"}}
    )
    reason: str = Field(min_length=3, max_length=500)


class HeldTransfers(BaseModel):
    items: list[TransferResponse]
    limit: int
    offset: int


class RiskFlag(BaseModel):
    id: int
    transfer_id: int
    rule_id: int
    action: Literal["hold", "flag"]
    rule_snapshot: dict
    explanation: str | None
    label: Literal["likely_ok", "review", "likely_fraud"] | None
    explained_at: datetime | None


class RiskFlags(BaseModel):
    items: list[RiskFlag]


class StatementQuestion(InputModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "How many transfers from 2026-01-01 to 2026-12-31?",
            }
        }
    )
    question: str = Field(min_length=3, max_length=500)


class QuestionSpec(InputModel):
    type: Annotated[int, Field(strict=True, ge=1, le=4)]
    counterparty: str | None = Field(default=None, min_length=1, max_length=100)
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")

    @model_validator(mode="after")
    def valid_question(self):
        if self.from_date > self.to_date or (self.to_date - self.from_date).days > 3660:
            raise ValueError("Invalid or excessive date range")
        if self.to_date == date.max:
            raise ValueError("End date is outside supported bounds")
        if (self.type == 1) != (self.counterparty is not None):
            raise ValueError("Only total-sent questions require a counterparty")
        return self


class StatementAnswer(BaseModel):
    type: int
    value: int
    unit: Literal["paise", "transfers"]
    currency: Literal["INR"] = "INR"
    transfer_ids: list[int]
    query: QuestionSpec
    provider: str


class FlagExplanation(InputModel):
    explanation: str = Field(min_length=10, max_length=1000)
    label: Literal["likely_ok", "review", "likely_fraud"]

    @model_validator(mode="after")
    def two_sentences(self):
        import re

        sentences = re.findall(r"[^.!?]+[.!?](?:\s|$)", self.explanation.strip())
        if len(sentences) != 2 or not self.explanation.rstrip().endswith((".", "!", "?")):
            raise ValueError("Exactly two sentences are required")
        return self


class ExplanationRequest(InputModel):
    model_config = ConfigDict(json_schema_extra={"example": {"limit": 10}})
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 10


class ExplanationResult(BaseModel):
    explained: int
    flag_ids: list[int]
