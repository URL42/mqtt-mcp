FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY server.py ./

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "server.py"]
