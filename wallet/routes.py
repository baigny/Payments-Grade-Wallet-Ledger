"""Compose feature routers; endpoint handlers live beside their services and schemas."""

from fastapi import APIRouter

from wallet.fraud.router import router as fraud_router
from wallet.reconciliation.router import router as reconciliation_router
from wallet.statements.router import router as statements_router
from wallet.transfers.router import router as transfers_router
from wallet.users.router import router as users_router
from wallet.wallets.router import router as wallets_router

router = APIRouter()
router.include_router(users_router)
router.include_router(wallets_router)
router.include_router(statements_router)
router.include_router(transfers_router)
router.include_router(fraud_router)
router.include_router(reconciliation_router)
