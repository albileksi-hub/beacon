FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pyproject declares what this depends on; requirements.txt decides which exact
# versions arrive, with a hash for every artefact. The floors in pyproject alone
# meant two people building this image a week apart got different software and
# neither could say what changed -- which for self-hosted analytics is the whole
# supply chain resting on whatever PyPI served that afternoon.
#
# Named requirements.txt, not requirements.lock, because Dependabot's pip
# ecosystem recognises the conventional name and there is no evidence it
# recognises the other. Get that wrong and the file the image actually
# installs from never receives a security update, while Dependabot happily
# bumps floors in pyproject that nobody installs from -- the supply chain
# work defeated silently, in the direction that still looks healthy.
#
# The lock is generated, never hand-edited, so it cannot drift from pyproject
# the way a hand-maintained one does; a test fails the build if a
# declared dependency is missing from it.
COPY pyproject.toml README.md requirements.txt ./
COPY app ./app
RUN pip install --upgrade pip \
 && pip install --require-hashes --no-deps -r requirements.txt \
 && pip install --no-deps .

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
