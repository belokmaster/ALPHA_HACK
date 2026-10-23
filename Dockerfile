FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
