from pydantic import BaseModel, ConfigDict, Field

from wallet.core.schemas import InputModel


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
