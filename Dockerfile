# Local supplier: docker build -t tacto-sim . && docker run --rm -p 8080:8080 tacto-sim
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE.md ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra sim
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra sim && useradd --create-home simulator
USER simulator
ENV PORT=8080
CMD ["sh", "-c", "exec /app/.venv/bin/tacto-sim serve --host 0.0.0.0 --port ${PORT}"]
