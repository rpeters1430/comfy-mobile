FROM python:3.15-slim@sha256:08a4c22c92b79ec821a7ad49768480039ee5b7accac3955228e75366e30abc8e
WORKDIR /app
COPY requirements.txt .
# Pick up Debian security fixes released since the base image was built, and
# drop pip after installing: the app never runs it, and its vendored copies of
# urllib3, msgpack and setuptools trail the patched releases.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y pip
COPY app /app
RUN chmod -R a+rX /app
USER 1000:1000
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/healthz', timeout=3)"]
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8095"]
