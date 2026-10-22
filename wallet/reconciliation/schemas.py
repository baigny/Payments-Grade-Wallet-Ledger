from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class ReconciliationRun(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    status: Literal["running", "passed", "failed", "error"]
    finding_count: int
    problem_count: int
    account_count: int
    transfer_count: int
    entry_count: int
    trigger_type: Literal["manual", "scheduled", "cli"]
    scheduled_for: date | None
    error_message: str | None


class ReconciliationFinding(BaseModel):
    id: int
    run_id: int
    check_name: str
    entity_type: str
    entity_id: int | None
    expected: dict
    actual: dict
    created_at: datetime


class ReconciliationFindings(BaseModel):
    items: list[ReconciliationFinding]
    limit: int
    offset: int
