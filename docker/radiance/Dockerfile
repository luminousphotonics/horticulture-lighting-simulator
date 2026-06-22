FROM ladybugtools/ladybug-radiance:0.2.11@sha256:be585d9ebea1045efe90202597877a555e3ceb142d02b79c0ff27bbf32c1407a

ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="rad-rebuild Radiance runtime" \
    org.opencontainers.image.description="Batch-style live Radiance runtime for rad-rebuild development and validation." \
    org.opencontainers.image.source="https://github.com/luminousphotonics/horticulture-lighting-simulator" \
    org.opencontainers.image.version="0.1.0" \
    org.opencontainers.image.base.name="docker.io/ladybugtools/ladybug-radiance:0.2.11@sha256:be585d9ebea1045efe90202597877a555e3ceb142d02b79c0ff27bbf32c1407a" \
    org.opencontainers.image.licenses="Apache-2.0"

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        fonts-dejavu \
        fonts-liberation \
        libfreetype6 \
        libpng16-16 \
        libjpeg62-turbo \
        procps \
        rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY docker/radiance/requirements.runtime.txt /tmp/requirements.runtime.txt

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.runtime.txt \
    && rm -f /tmp/requirements.runtime.txt

ENV PATH="/opt/venv/bin:/usr/local/radiance/bin:/home/ladybugbot/bin:${PATH}"
ENV PYTHONPATH="/workspace/src"
ENV RAYPATH=".:/usr/local/radiance/lib"
ENV MPLBACKEND="Agg"
ENV MPLCONFIGDIR="/tmp/matplotlib"
ENV XDG_CACHE_HOME="/tmp/rad-rebuild/cache"
ENV TMPDIR="/tmp/rad-rebuild/tmp"
RUN printf '%s\n' \
    'export PATH="/home/ladybugbot/bin:/usr/local/radiance/bin:$PATH"' \
    > /etc/profile.d/radiance.sh

COPY pyproject.toml /workspace/pyproject.toml
COPY src/rad_rebuild/__init__.py src/rad_rebuild/py.typed /workspace/src/rad_rebuild/
COPY src/rad_rebuild/radiance /workspace/src/rad_rebuild/radiance
COPY data/radiance/curve_data /workspace/data/radiance/curve_data
COPY scripts/radiance /workspace/scripts/radiance
COPY scripts/radiance/reproduce.sh /workspace/reproduce.sh

RUN chmod +x /workspace/scripts/radiance/*.sh /workspace/reproduce.sh \
    && mkdir -p /workspace/data/radiance/ies_sources /workspace/outputs/radiance /out /tmp/matplotlib /tmp/rad-rebuild/cache/fontconfig /tmp/rad-rebuild/tmp \
    && chown -R ladybugbot:ladybugbot /workspace /out /tmp/matplotlib /tmp/rad-rebuild

USER ladybugbot

CMD ["/bin/bash"]
