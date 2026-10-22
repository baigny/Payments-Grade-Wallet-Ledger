from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request

from wallet.core import idempotency
from wallet.core.api import WRITE_RESPONSES, ResourceID
from wallet.core.auth import CurrentActor, WriteKey, authorize_owner
from wallet.core.schemas import MAX_MONEY
from wallet.statements import questions
from wallet.statements import service as statements
from wallet.statements.schemas import (
    StatementAnswer,
    StatementQuestion,
    StatementResponse,
    StatementSummary,
)

router = APIRouter()


@router.get("/wallets/{wallet_id}/statement", response_model=StatementResponse, tags=["Wallets"])
def statement(
    wallet_id: ResourceID,
    actor: CurrentActor,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_MONEY)] = 0,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
):
    """Newest first. Follow next_cursor for stable seek pagination during new postings.

    Offset remains available for numbered pages. Cursor and nonzero offset cannot be combined.
    Cursors are untrusted seek positions bound to a wallet, not credentials or snapshot tokens.
    """
    return statements.statement(actor, wallet_id, limit, offset, cursor)


@router.get(
    "/wallets/{wallet_id}/statement/summary", response_model=StatementSummary, tags=["Wallets"]
)
def statement_summary(wallet_id: ResourceID, from_date: date, to_date: date, actor: CurrentActor):
    """Opening and closing ledger balance, gross credits/debits and transfer count.

    Dates are inclusive UTC dates based on entry creation time. One read-only Repeatable Read
    snapshot supplies all totals. Held transfers have no entries and contribute nothing.
    Reversals appear as new movements; cached account balances and LLMs are not used.
    """
    return statements.statement_summary(actor, wallet_id, from_date, to_date)


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
    Hosted quota exhaustion returns 503 LLM_BUSY with Retry-After. Retry using the same key;
    infrastructure failures are not saved as completed idempotent responses.
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
