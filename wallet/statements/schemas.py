from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wallet.core.schemas import InputModel


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
