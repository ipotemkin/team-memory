# Образ бэкенда (API, воркер и beat — один образ, разные команды в docker-compose.yml).
FROM python:3.12-slim
# Бинарник uv из официального образа.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# venv вне /app: монтирование исходников (api-test) его не перекрывает.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
# Сначала только зависимости — слой кешируется, пока не меняются pyproject/uv.lock.
COPY pyproject.toml uv.lock* ./
# По умолчанию без dev-зависимостей; api-test передаёт пустое значение (нужны pytest и т.п.).
ARG UV_SYNC_ARGS=--no-dev
RUN uv sync --frozen $UV_SYNC_ARGS
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
