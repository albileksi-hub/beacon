FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency metadata and the package itself. pyproject is the single source of
# truth for what gets installed, so there is no requirements.txt to drift.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

COPY migrations ./migrations
COPY static ./static
COPY alembic.ini manage.py seed.py bench.py ./

# Never run the application as root.
RUN useradd --create-home --uid 1000 beacon && chown -R beacon:beacon /app
USER beacon

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as r, sys; sys.exit(0 if r.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

# Migrations run on start, so a deploy that changes the schema needs no separate
# step. Hosts inject PORT, hence the indirection through a shell.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
