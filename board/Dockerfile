FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir /data && chown 65534:65534 /data
COPY . .
USER 65534:65534
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=4s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz',timeout=3)"
CMD ["python","server.py"]
