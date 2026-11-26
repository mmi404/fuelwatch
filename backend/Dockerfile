FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml constraints.txt ./
COPY fuelwatch ./fuelwatch
RUN pip install --no-cache-dir --retries 5 --timeout 60 -c constraints.txt .

RUN useradd --system --create-home fuelwatch && mkdir /data && chown fuelwatch /data
USER fuelwatch
ENV DATABASE_PATH=/data/fuelwatch.db
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8080/livez', timeout=2)"
CMD ["uvicorn", "fuelwatch.main:app", "--host", "0.0.0.0", "--port", "8080"]
