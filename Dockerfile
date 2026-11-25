FROM python:3.15-slim@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5
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
