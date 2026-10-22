# Application structure

The backend is organized by feature. FastAPI supports packages and `APIRouter`
composition; it does not prescribe one mandatory folder layout. Feature packages
keep related HTTP handlers, validation and business operations together as this
project grows. See the [FastAPI guide](https://fastapi.tiangolo.com/tutorial/bigger-applications/).

```text
wallet/
  main.py                 application, health endpoints and error handlers
  routes.py               assembles the six feature routers
  bootstrap.py            local administrator CLI
  migrate.py              migration CLI and runtime-role provisioning
  worker.py               nightly reconciliation and explanation scheduling
  core/
    api.py                shared endpoint types and response metadata
    auth.py               bearer authentication and authorization dependencies
    db.py                 PostgreSQL connection setup
    errors.py             shared domain exception
    idempotency.py        request transaction, savepoints and exact replay
    llm.py                mock and Ollama provider interface
    schemas.py            shared validation types and error response
  users/
    router.py             user creation endpoint
    schemas.py            user request and response DTOs
    service.py            user creation and token issuance
  wallets/
    router.py             wallet creation, listing and balance endpoints
    schemas.py            wallet request and response DTOs
    service.py            wallet operations and ownership checks
  transfers/
    router.py             payment, reversal and detail endpoints
    schemas.py            transfer request and response DTOs
    service.py            ordered locks, posting and review transitions
  fraud/
    router.py             rules, holds, review and explanation endpoints
    schemas.py            rule, review and explanation DTOs
    service.py            rule evaluation, configuration and review queries
    explanations.py       advisory explanation processing
  statements/
    router.py             statement pages, summaries and question endpoint
    schemas.py            page, summary and question DTOs
    service.py            ledger reads and summary snapshots
    pagination.py         validated wallet-bound cursor positions
    questions.py          validated intent and parameterized ledger queries
  reconciliation/
    router.py             manual runs and report endpoints
    schemas.py            run and finding response DTOs
    service.py            snapshot, checks, reports and CLI implementation
    queries.py            independently readable reconciliation SQL
    __main__.py           preserves python -m wallet.reconciliation
```

Every directory has an `__init__.py`, making it a Python package. These files
do not hide service imports or perform application setup.

## Mapping from Node.js terminology

| Familiar role | Python implementation |
|---|---|
| Controller | Feature `router.py`: HTTP handling, dependencies and response models |
| Request/response DTO | Feature `schemas.py`: Pydantic validation and serialization |
| Service | Feature `service.py`: business operations and explicit SQL |
| Guards | Shared authentication and ownership dependencies in `core/auth.py` |
| Module registration | `routes.py` composes feature `APIRouter` instances |

The schema files describe API data, not ORM entities. Database structure and
constraints live in root-level `migrations/`. One explicit transaction spans a
write request, its idempotency record, posting and audit history. Feature services
receive that connection; importing a package does not start a transaction.

Fraud review endpoints delegate releases and rejections to the transfer service,
so the account-lock order and balance checks stay in one money path. Statement
reads reuse wallet authorization. The worker invokes reconciliation and advisory
explanations separately. This is one application with internal feature boundaries,
not a set of separately deployed services.

The existing service functions remain functions: classes and an extra repository
layer are unnecessary for this explicit-SQL design. Shared code belongs in `core`
only when several features use it. Feature-specific rules and models stay beside
their endpoints. Public endpoint paths, validation, response shapes, SQL and
database migrations are preserved by the restructuring.
