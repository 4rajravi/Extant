.PHONY: up down bootstrap check test lint

# Start local Postgres and wait until it accepts connections.
up:
	docker compose up -d --wait

# Stop it. The named volume, and so the data, is kept; `docker compose down -v` drops it.
down:
	docker compose down

# Create the vector extension in public, the extant_reader role and public.vec_cache.
bootstrap:
	uv run python -m extant.core.schema bootstrap

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest

check: lint test
