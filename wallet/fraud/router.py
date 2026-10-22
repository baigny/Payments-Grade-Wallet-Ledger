from typing import Annotated

from fastapi import APIRouter, Query, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES, ResourceID
from wallet.core.auth import CurrentActor, WriteKey, require_admin
from wallet.core.schemas import MAX_MONEY
from wallet.fraud import explanations
from wallet.fraud import service as fraud
from wallet.fraud.schemas import (
    ExplanationRequest,
    ExplanationResult,
    HeldTransfers,
    ReviewDecision,
    RiskFlags,
    RiskRule,
    RiskRules,
    RiskRuleUpdate,
)
from wallet.transfers import service as transfers
from wallet.transfers.schemas import TransferResponse

router = APIRouter()


@router.post(
    "/admin/risk-flags/explain",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=ExplanationResult,
    tags=["Fraud"],
)
def explain_flags(data: ExplanationRequest, request: Request, actor: CurrentActor, key: WriteKey):
    """Generate advisory explanations; never change money or transfer status."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        ExplanationResult,
        lambda conn: explanations.explain(conn, data.limit),
        data.model_dump(mode="json"),
    )


@router.get("/admin/risk-rules", response_model=RiskRules, tags=["Fraud"])
def risk_rules(actor: CurrentActor):
    require_admin(actor)
    return fraud.rules()


@router.post(
    "/admin/risk-rules/{rule_id}",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=RiskRule,
    tags=["Fraud"],
)
def update_risk_rule(
    rule_id: ResourceID, data: RiskRuleUpdate, request: Request, actor: CurrentActor, key: WriteKey
):
    """Change thresholds/actions and save an actor-attributed configuration event."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        RiskRule,
        lambda conn: fraud.update_rule(conn, actor, rule_id, data),
        data.model_dump(mode="json"),
    )


@router.get("/admin/transfers/held", response_model=HeldTransfers, tags=["Fraud"])
def held_transfers(
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_MONEY)] = 0,
):
    require_admin(actor)
    return fraud.held(limit, offset)


@router.get("/admin/transfers/{transfer_id}/risk-flags", response_model=RiskFlags, tags=["Fraud"])
def transfer_flags(transfer_id: ResourceID, actor: CurrentActor):
    require_admin(actor)
    return fraud.flags(transfer_id)


@router.post(
    "/admin/transfers/{transfer_id}/release",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=TransferResponse,
    tags=["Fraud"],
)
def release_transfer(
    transfer_id: ResourceID,
    data: ReviewDecision,
    request: Request,
    actor: CurrentActor,
    key: WriteKey,
):
    """Admin releases an existing hold after rechecking funds. Rules are explicitly overridden."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        TransferResponse,
        lambda conn: transfers.review(conn, actor, transfer_id, True, data.reason),
        data.model_dump(mode="json"),
    )


@router.post(
    "/admin/transfers/{transfer_id}/reject",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=TransferResponse,
    tags=["Fraud"],
)
def reject_transfer(
    transfer_id: ResourceID,
    data: ReviewDecision,
    request: Request,
    actor: CurrentActor,
    key: WriteKey,
):
    """Admin rejects a hold without creating entries or changing balances."""
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        TransferResponse,
        lambda conn: transfers.review(conn, actor, transfer_id, False, data.reason),
        data.model_dump(mode="json"),
    )
