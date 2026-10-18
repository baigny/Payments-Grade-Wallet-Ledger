from typing import Annotated

from fastapi import APIRouter, Path, Query

from wallet import accounts, transfers
from wallet.auth import CurrentActor, WriteKey
from wallet.models import (
    MAX_MONEY,
    StatementResponse,
    TransferCreate,
    TransferResponse,
    UserCreate,
    UserCreated,
    WalletCreate,
    WalletList,
    WalletResponse,
)

router = APIRouter()
ResourceID = Annotated[int, Path(ge=1, le=MAX_MONEY)]


@router.post("/users", status_code=201, response_model=UserCreated, tags=["Users"])
def create_user(data: UserCreate, actor: CurrentActor, _key: WriteKey):
    """Admin creates a user and receives a one-time bearer token."""
    return accounts.create_user(actor, data)


@router.post(
    "/users/{user_id}/wallets", status_code=201, response_model=WalletResponse, tags=["Wallets"]
)
def create_wallet(user_id: ResourceID, data: WalletCreate, actor: CurrentActor, _key: WriteKey):
    """Create an empty INR wallet for the caller, or for any user as an admin."""
    return accounts.create_wallet(actor, user_id, data.name)


@router.get("/users/{user_id}/wallets", response_model=WalletList, tags=["Wallets"])
def list_wallets(user_id: ResourceID, actor: CurrentActor):
    return accounts.list_wallets(actor, user_id)


@router.get("/wallets/{wallet_id}/balance", response_model=WalletResponse, tags=["Wallets"])
def balance(wallet_id: ResourceID, actor: CurrentActor):
    return accounts.balance(actor, wallet_id)


@router.get("/wallets/{wallet_id}/statement", response_model=StatementResponse, tags=["Wallets"])
def statement(
    wallet_id: ResourceID,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return accounts.statement(actor, wallet_id, limit, offset)


@router.post("/transfers", status_code=201, response_model=TransferResponse, tags=["Transfers"])
def create_transfer(data: TransferCreate, actor: CurrentActor, _key: WriteKey):
    """Post a peer payment or withdrawal from an owned wallet. Admins may top up or charge fees.

    Amounts are integer paise. Top-up needs destination_account_id; withdrawal and fee need
    source_account_id; peer needs both. System endpoints are resolved by the server.
    """
    return transfers.create_transfer(actor, data)


@router.post(
    "/transfers/{transfer_id}/reversal",
    status_code=201,
    response_model=TransferResponse,
    tags=["Transfers"],
)
def reverse_transfer(transfer_id: ResourceID, actor: CurrentActor, _key: WriteKey):
    """Admin posts one full reversal. The original and its entries remain immutable."""
    return transfers.reverse_transfer(actor, transfer_id)
