from typing import Annotated

from fastapi import APIRouter, Query, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES, ResourceID
from wallet.core.auth import CurrentActor, WriteKey, require_admin
from wallet.core.schemas import MAX_MONEY
from wallet.reconciliation import service as reconciliation
from wallet.reconciliation.schemas import ReconciliationFindings, ReconciliationRun

router = APIRouter()


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
