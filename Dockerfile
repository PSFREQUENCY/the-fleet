FROM python:3.12-slim

WORKDIR /app

# Dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source
COPY *.py ./

# State volume mount point
VOLUME ["/app/data"]
ENV FLEET_DB=/app/data/fleet.enc

CMD ["python", "fleet.py"]
