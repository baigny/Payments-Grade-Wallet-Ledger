import logging
import time
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wallet.db import connect

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(title="Wallet Ledger", version="0.1.0")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    logger.info(
        '{"request_id":"%s","method":"%s","status":%s,"duration_ms":%.2f}',
        request.state.request_id,
        request.method,
        response.status_code,
        (time.monotonic() - started) * 1000,
    )
    return response


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
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
