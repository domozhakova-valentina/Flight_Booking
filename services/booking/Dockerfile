FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /project

COPY requirements-dev.txt ./
COPY services/booking/requirements.txt ./services/booking/requirements.txt
RUN pip install --no-cache-dir -r services/booking/requirements.txt

COPY proto ./proto
COPY services/booking ./services/booking
RUN python proto/generate.py booking

WORKDIR /project/services/booking
CMD ["python", "-m", "app.main"]
