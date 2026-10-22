from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_MONEY = 2**63 - 1


AccountID = Annotated[int, Field(strict=True, ge=1, le=MAX_MONEY)]


Amount = Annotated[int, Field(strict=True, gt=0, le=MAX_MONEY)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
