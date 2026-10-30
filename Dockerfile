# The pystiltctl image: PYSTILT (its wheel carries a static HYSPLIT build),
# pystiltctl, and the libraries for s3:// and gs:// buckets, installed from
# uv.lock. Each pod of a Job runs `pystiltctl worker` in it.
#
#   docker build --build-arg VERSION=0.1.0 -t pystiltctl .
#   docker run --rm pystiltctl pystiltctl --help

FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never
# setuptools-scm reads the version from git, and the build leaves .git out.
ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYSTILTCTL=${VERSION}
WORKDIR /src
# The dependencies first, so a change to the code alone reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --extra s3 --extra gcs
COPY . .
RUN uv sync --locked --no-dev --no-editable --extra s3 --extra gcs

FROM python:3.13-slim-bookworm
# tini runs as process 1 and passes Kubernetes' stop signal on to stilt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TZ=UTC
RUN useradd --create-home --uid 10001 stilt
USER 10001
WORKDIR /home/stilt
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["pystiltctl", "--help"]
