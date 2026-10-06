FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 reelarr \
    && useradd --uid 10001 --gid reelarr --no-create-home reelarr \
    && mkdir /data && chown reelarr:reelarr /data && chmod 700 /data
COPY --chown=reelarr:reelarr app ./app
USER reelarr
EXPOSE 8282
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8282/healthz', timeout=4)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8282", "--workers", "1", "--no-access-log"]
