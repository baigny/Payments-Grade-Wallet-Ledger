from typing import Annotated

from fastapi import APIRouter, Body, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES, ResourceID
from wallet.core.auth import CurrentActor, WriteKey, require_admin
from wallet.transfers import service as transfers
from wallet.transfers.schemas import TRANSFER_EXAMPLES, TransferCreate, TransferResponse

router = APIRouter()


@router.post(
    "/transfers",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=TransferResponse,
    tags=["Transfers"],
)
def create_transfer(
    data: Annotated[TransferCreate, Body(openapi_examples=TRANSFER_EXAMPLES)],
    request: Request,
    actor: CurrentActor,
    key: WriteKey,
):
    """Post a peer payment or withdrawal from an owned wallet. Admins may top up or charge fees.

    Amounts are integer paise. Top-up needs destination_account_id; withdrawal and fee need
    source_account_id; peer needs both. System endpoints are resolved by the server.
    """
    if data.type in ("top_up", "fee"):
        require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        TransferResponse,
        lambda conn: transfers.create_transfer(conn, actor, data),
        data.model_dump(mode="json"),
    )


@router.post(
    "/transfers/{transfer_id}/reversal",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=TransferResponse,
    tags=["Transfers"],
)
def reverse_transfer(transfer_id: ResourceID, request: Request, actor: CurrentActor, key: WriteKey):
    """Admin posts one full reversal. The original and its entries remain immutable."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        TransferResponse,
        lambda conn: transfers.reverse_transfer(conn, actor, transfer_id),
    )


@router.get("/transfers/{transfer_id}", response_model=TransferResponse, tags=["Transfers"])
def transfer_detail(transfer_id: ResourceID, actor: CurrentActor):
    """Read current state as an account owner or admin; replay bodies remain original snapshots."""
    return transfers.get_transfer(actor, transfer_id)
