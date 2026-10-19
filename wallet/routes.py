from typing import Annotated

from fastapi import APIRouter, Body, Path, Query, Request

from wallet import accounts, explanations, fraud, idempotency, questions, reconciliation, transfers
from wallet.auth import CurrentActor, WriteKey, authorize_owner, require_admin
from wallet.models import (
    MAX_MONEY,
    TRANSFER_EXAMPLES,
    ExplanationRequest,
    ExplanationResult,
    HeldTransfers,
    ReconciliationFindings,
    ReconciliationRun,
    ReviewDecision,
    RiskFlags,
    RiskRule,
    RiskRules,
    RiskRuleUpdate,
    StatementAnswer,
    StatementQuestion,
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
WRITE_RESPONSES = {
    201: {
        "headers": {
            "Idempotent-Replay": {
                "description": "false for the initial response; true for a saved response",
                "schema": {"type": "string", "enum": ["true", "false"]},
            }
        }
    }
}
ResourceID = Annotated[int, Path(ge=1, le=MAX_MONEY)]


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
        lambda conn: accounts.create_user(conn, actor, data),
        data.model_dump(mode="json"),
    )


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


@router.get("/wallets/{wallet_id}/statement", response_model=StatementResponse, tags=["Wallets"])
def statement(
    wallet_id: ResourceID,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_MONEY)] = 0,
):
    return accounts.statement(actor, wallet_id, limit, offset)


@router.post(
    "/users/{user_id}/statement/ask",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=StatementAnswer,
    tags=["Statement questions"],
)
def ask_statement(
    user_id: ResourceID,
    data: StatementQuestion,
    request: Request,
    actor: CurrentActor,
    key: WriteKey,
):
    """Answer one of four supported questions from posted movements in the user's own wallets.

    Dates are inclusive UTC dates. Values are gross posted movements, including top-ups and
    reversals; counts deduplicate transfers touching two of the same user's wallets.
    At most 1000 contributing movements; narrow wider results. No ledger rows reach the
    statement-intent provider. The default provider is an offline mock, not a learned model.
    """
    authorize_owner(actor, user_id)
    return idempotency.execute(
        actor,
        key,
        request,
        StatementAnswer,
        lambda conn: questions.ask(conn, actor, user_id, data.question),
        data.model_dump(mode="json"),
    )


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


@router.post(
    "/admin/reconciliation/run",
    status_code=201,
    responses=WRITE_RESPONSES,
    response_model=ReconciliationRun,
    tags=["Reconciliation"],
)
def reconcile(request: Request, actor: CurrentActor, key: WriteKey):
    """Admin checks one Repeatable Read snapshot. Reports problems without repairing money.

    finding_count counts raw invariant violations; problem_count compares ledger and caches
    against posted business events. A single damaged entry can violate several invariants.
    The run returns passed, failed (integrity problems), or error (checks could not complete).
    """
    require_admin(actor)
    return idempotency.execute(
        actor,
        key,
        request,
        ReconciliationRun,
        lambda conn: reconciliation.run(conn),
        configure=reconciliation.configure,
    )


@router.get(
    "/admin/reconciliation/runs/{run_id}",
    response_model=ReconciliationRun,
    tags=["Reconciliation"],
)
def reconciliation_report(run_id: ResourceID, actor: CurrentActor):
    require_admin(actor)
    return reconciliation.report(run_id)


@router.get(
    "/admin/reconciliation/runs/{run_id}/findings",
    response_model=ReconciliationFindings,
    tags=["Reconciliation"],
)
def reconciliation_findings(
    run_id: ResourceID,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_MONEY)] = 0,
):
    """Paginated raw invariant violations, including downstream consequences of corruption."""
    require_admin(actor)
    return reconciliation.findings(run_id, limit, offset)


@router.get(
    "/admin/reconciliation/runs/{run_id}/problems",
    response_model=ReconciliationFindings,
    tags=["Reconciliation"],
)
def reconciliation_problems(
    run_id: ResourceID,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_MONEY)] = 0,
):
    """Compare entries and stored balances against immutable posted transfer records."""
    require_admin(actor)
    return reconciliation.findings(run_id, limit, offset, problems=True)
