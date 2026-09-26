# Portable API image: application source is baked in, not bind-mounted.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin secretary
COPY api/ /app/api/
USER 10001:10001
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "api.secretary_api:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
