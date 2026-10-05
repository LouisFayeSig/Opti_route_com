FROM python:3.12-slim AS builder

ENV POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1

WORKDIR /build
RUN python -m pip install --no-cache-dir "poetry==${POETRY_VERSION}"
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --only main --no-root --no-ansi


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
WORKDIR /app

COPY --from=builder /build/.venv /opt/venv
COPY app.py ./
COPY src ./src
RUN mkdir -p /app/.cache /app/.streamlit && chown -R app:app /app

USER 10001:10001
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.maxUploadSize=20", "--server.enableXsrfProtection=true"]
