from fastapi import APIRouter, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES, ResourceID
from wallet.core.auth import CurrentActor, WriteKey, authorize_owner
from wallet.wallets import service as accounts
from wallet.wallets.schemas import WalletCreate, WalletList, WalletResponse

router = APIRouter()


@router.post(
    "/users/{user_id}/wallets",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=WalletResponse,
    tags=["Wallets"],
)
def create_wallet(
    user_id: ResourceID,
    data: WalletCreate,
    request: Request,
    actor: CurrentActor,
    key: WriteKey,
):
    """Create an empty INR wallet for the caller, or for any user as an admin."""
    authorize_owner(actor, user_id)
    return idempotency.execute(
        actor,
        key,
        request,
        WalletResponse,
        lambda conn: accounts.create_wallet(conn, actor, user_id, data.name),
        data.model_dump(mode="json"),
    )


@router.get("/users/{user_id}/wallets", response_model=WalletList, tags=["Wallets"])
def list_wallets(user_id: ResourceID, actor: CurrentActor):
    return accounts.list_wallets(actor, user_id)


@router.get("/wallets/{wallet_id}/balance", response_model=WalletResponse, tags=["Wallets"])
def balance(wallet_id: ResourceID, actor: CurrentActor):
    return accounts.balance(actor, wallet_id)
