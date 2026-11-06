# The application sources as the release ships them: without the test suite,
# the demo seeders (a fixed, public admin password) and the test-only tools.
# Pruned in a stage of their own so the files never reach a release layer;
# the test stage below copies the full tree.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS release-source
WORKDIR /src
COPY api/ api/
COPY tools/ tools/
COPY deploy/ deploy/
COPY js/ js/
RUN rm -rf api/tests tools/test_database.py deploy/test-init.sh js/__tests__

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS runtime
WORKDIR /app
COPY requirements.txt .
# Debian security updates are applied at build time: the base image lags behind
# them, and without this the release image shipped known-vulnerable system
# packages (scripts/security-scan.sh found six in perl-base alone).
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home partnertype
COPY --from=release-source /src/api/ api/
COPY --from=release-source /src/tools/ tools/
COPY migrations/ migrations/
COPY locales/ locales/
COPY --from=release-source /src/deploy/ deploy/
COPY server.py gunicorn.conf.py alembic.ini Modelfile ./
COPY index.html register.html set-password.html manifest.json sw.js ./
COPY pages/ pages/
COPY --from=release-source /src/js/ js/
COPY css/ css/
COPY img/ img/
USER 10001
CMD ["gunicorn", "--config", "gunicorn.conf.py", "server:create_app()"]

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY api/ api/
COPY tools/ tools/
COPY migrations/ migrations/
COPY locales/ locales/
COPY deploy/ deploy/
COPY server.py gunicorn.conf.py alembic.ini pytest.ini .coveragerc ./
COPY scripts/seed_demo.py scripts/seed_demo.py
COPY index.html register.html set-password.html manifest.json sw.js ./
COPY pages/ pages/
COPY js/ js/
COPY css/ css/
COPY img/ img/
# The committed OpenAPI description, pinned to the generator by a test.
COPY docs/openapi.json docs/openapi.json
CMD ["python", "-m", "pytest"]

FROM runtime AS release
