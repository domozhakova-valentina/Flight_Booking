FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /project

COPY requirements-dev.txt ./
COPY services/flight/requirements.txt ./services/flight/requirements.txt
RUN pip install --no-cache-dir -r services/flight/requirements.txt

COPY proto ./proto
COPY services/flight ./services/flight
RUN python proto/generate.py flight

WORKDIR /project/services/flight
CMD ["python", "-m", "app.main"]
