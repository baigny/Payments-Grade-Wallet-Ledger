from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wallet.core.schemas import Amount, InputModel
from wallet.transfers.schemas import TransferResponse


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
