FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/wallet-pytest-cache"
ENV RUFF_CACHE_DIR=/tmp/wallet-ruff-cache
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY wallet ./wallet
RUN pip install --no-cache-dir --no-deps .
COPY migrations ./migrations
COPY tests ./tests
COPY scripts ./scripts
COPY docs/api ./docs/api
COPY docs/statement-questions.json ./docs/statement-questions.json
COPY postman ./postman
RUN useradd --create-home wallet
USER wallet
CMD ["uvicorn", "wallet.main:app", "--host", "0.0.0.0", "--port", "8000"]
