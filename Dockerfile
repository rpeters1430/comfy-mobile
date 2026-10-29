FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app /app
RUN chmod -R a+rX /app
USER 1000:1000
EXPOSE 8095
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8095"]
