.PHONY: install dev redis stop-redis demo test lint

install:
	uv sync

dev:
	uv run uvicorn agent_bus.main:app --host 0.0.0.0 --port 18080 --reload

redis:
	docker compose up -d redis

stop-redis:
	docker compose down

demo:
	@python examples/client_demo.py

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check agent_bus examples || true
	uv run pyright agent_bus || true
