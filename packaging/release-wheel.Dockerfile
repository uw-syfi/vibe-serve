FROM ghcr.io/astral-sh/uv:0.9.24 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv
COPY vibesys-*.whl /tmp/
COPY verify_installed_release.py /verify_installed_release.py

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/tmp/verify-home \
    INSTALL_HOME=/tmp/install-home \
    PATH=/tmp/vibesys-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH="" \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_DOWNLOADS=never \
    UV_TOOL_BIN_DIR=/tmp/vibesys-bin \
    UV_TOOL_DIR=/tmp/vibesys-tools

RUN mkdir -p "$HOME" "$INSTALL_HOME" "$UV_CACHE_DIR" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" \
    && test -z "$(find "$HOME" -mindepth 1 -print -quit)" \
    && test -z "$(find "$INSTALL_HOME" -mindepth 1 -print -quit)" \
    && set -- /tmp/vibesys-*.whl \
    && test "$#" -eq 1 \
    && HOME="$INSTALL_HOME" uv tool install --no-cache --no-config --python /usr/local/bin/python "$1" \
    && rm "$1" \
    && test -z "$(find /tmp -maxdepth 1 -name 'vibesys-*.whl' -print -quit)" \
    && test -z "$(find "$HOME" -mindepth 1 -print -quit)"

WORKDIR /tmp

CMD ["/tmp/vibesys-tools/vibesys/bin/python", "/verify_installed_release.py"]
