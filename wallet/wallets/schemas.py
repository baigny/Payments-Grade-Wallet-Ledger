from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wallet.core.schemas import InputModel


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
