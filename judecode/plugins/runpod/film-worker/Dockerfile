# Runpod hosts are x86_64. Build with --platform=linux/amd64.
# CUDA/PyTorch are inherited rather than reinstalled in a second layer.
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/tmp/jude-film/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    JUDE_FILM_TMP=/tmp/jude-film

WORKDIR /opt/jude-film-worker

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin jude-film \
    && mkdir -p /tmp/jude-film \
    && chown -R 10001:10001 /tmp/jude-film /opt/jude-film-worker

COPY --chown=10001:10001 app.py ./app.py
USER 10001:10001

# This Pod intentionally exposes only the authenticated inference API; SSH/Jupyter
# are not needed by the client-side Runpod operations agent.
EXPOSE 8000
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
