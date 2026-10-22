from fastapi import APIRouter, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES
from wallet.core.auth import CurrentActor, WriteKey, require_admin
from wallet.users import service as users
from wallet.users.schemas import UserCreate, UserCreated

router = APIRouter()


@router.post(
    "/users", status_code=201, responses=WRITE_RESPONSES, response_model=UserCreated, tags=["Users"]
)
def create_user(data: UserCreate, request: Request, actor: CurrentActor, key: WriteKey):
    """Admin creates a user. An identical retry replays the original token response."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        UserCreated,
        lambda conn: users.create_user(conn, actor, data),
        data.model_dump(mode="json"),
    )
