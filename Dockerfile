# syntax=docker/dockerfile:1
#
# Dockerfile
#
# Builds the package into a slim image that runs its console script.
#
# Build arguments:
#   PYTHON_VERSION  required; the MAJOR.MINOR in .python-version.
#
# Build secrets:
#   gh_token  optional; a GitHub token that can read the private git
#             dependencies. It is read only inside the RUN steps that fetch
#             dependencies and is never written to a layer.
#
# Example:
#   docker build --build-arg PYTHON_VERSION="$(cat .python-version)" \
#     --secret id=gh_token,env=GH_TOKEN -t python-repo-template .

ARG PYTHON_VERSION
ARG DEBIAN_RELEASE=trixie

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS build

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

# uv needs git for git dependencies.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Runs a command with git signed in to GitHub from the gh_token secret, when
# one is mounted. The token stays in this process's environment only.
COPY --chmod=755 <<"EOF" /usr/local/bin/with-gh-token
#!/bin/sh
set -eu
if [ -s /run/secrets/gh_token ]; then
    GIT_CONFIG_COUNT=1
    GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/gh_token)@github.com/.insteadOf"
    GIT_CONFIG_VALUE_0="https://github.com/"
    export GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
fi
exec "$@"
EOF

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src

# Dependencies first, so a code change doesn't re-fetch them. --locked fails
# the build if uv.lock is out of date with pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=gh_token \
    with-gh-token uv sync --locked --no-dev --no-install-project

# Then the project itself, installed as a wheel so the final image needs no
# source.
COPY . .
RUN --mount=type=secret,id=gh_token \
    with-gh-token uv sync --locked --no-dev --no-editable

# The same Python image, so the venv's interpreter path still resolves.
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE}

RUN useradd --create-home --uid 10001 app

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER app
WORKDIR /home/app

ENTRYPOINT ["python-repo-template"]
