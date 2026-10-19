import logging
import time
from typing import Literal
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from wallet.db import connect
from wallet.errors import DomainError
from wallet.models import ErrorResponse
from wallet.routes import router

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(
    title="Wallet Ledger",
    version="0.5.0",
    description=(
        "INR wallet backend with bearer authentication and explicit SQL. "
        "Every POST requires a UUID Idempotency-Key, scoped to the authenticated caller. "
        "Identical retries return the saved status and JSON body with Idempotent-Replay: true. "
        "Changing the endpoint, resource or validated body with the same key returns 422. "
        "Concurrent duplicates wait up to five seconds; a still-busy key returns 409. "
        "Business failures are saved; validation, authentication and infrastructure "
        "failures are not."
    ),
    openapi_tags=[
        {"name": "Health", "description": "Application liveness and database readiness."},
        {"name": "Users", "description": "Administrator-managed user creation."},
        {"name": "Wallets", "description": "Wallet ownership, balances and statements."},
        {"name": "Transfers", "description": "Atomic, double-entry money movements."},
        {"name": "Fraud", "description": "Database rules and explicit admin review of holds."},
        {
            "name": "Statement questions",
            "description": "Validated intent and ledger-backed answers.",
        },
        {"name": "Reconciliation", "description": "Admin-only integrity checks and saved reports."},
    ],
    responses={status: {"model": ErrorResponse} for status in (400, 401, 403, 404, 409, 422, 503)},
)


class LiveStatus(BaseModel):
    status: Literal["ok"]


class ReadyStatus(BaseModel):
    status: Literal["ready"]


def error_response(request: Request, status: int, code: str, message: str):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "request_id": request_id},
        headers=headers,
    )


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return error_response(request, exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if any(error["loc"][:2] == ("header", "Idempotency-Key") for error in exc.errors()):
        return error_response(request, 400, "INVALID_IDEMPOTENCY_KEY", "A UUID key is required")
    return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(psycopg.OperationalError)
async def database_error(request: Request, exc: psycopg.OperationalError):
    logger.error("Database connection or transaction unavailable: %s", type(exc).__name__)
    return error_response(
        request, 503, "DATABASE_UNAVAILABLE", "Database is temporarily unavailable"
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("Unexpected request failure", exc_info=exc)
    return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        '{"request_id":"%s","method":"%s","status":%s,"duration_ms":%.2f}',
        request.state.request_id,
        request.method,
        response.status_code,
        (time.monotonic() - started) * 1000,
    )
    return response


@app.get(
    "/health/live", tags=["Health"], summary="Check application liveness", response_model=LiveStatus
)
def live():
    return {"status": "ok"}


@app.get(
    "/health/ready",
    tags=["Health"],
    summary="Check database and schema readiness",
    response_model=ReadyStatus,
    responses={503: {"model": ErrorResponse, "description": "Database or schema is unavailable"}},
)
def ready(request: Request):
    try:
        with connect(connect_timeout=3) as conn:
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
    except psycopg.Error:
        return JSONResponse(
            status_code=503,
            content={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Database or schema is not ready",
                "request_id": request.state.request_id,
            },
        )
    return {"status": "ready"}


app.include_router(router)
