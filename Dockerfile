ARG RAKEPROOF_DEPS_IMAGE=python:3.11-slim
FROM ${RAKEPROOF_DEPS_IMAGE} AS dependencies
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RAKEPROOF_DATA=/var/lib/rakeproof PORT=8000
WORKDIR /srv/rakeproof
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt
COPY rakeproof rakeproof
COPY image_lab image_lab
COPY static static
COPY templates templates
EXPOSE 8000
HEALTHCHECK --interval=4s --timeout=3s --retries=8 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/rake/health').read()"]
CMD ["python", "-m", "rakeproof"]
