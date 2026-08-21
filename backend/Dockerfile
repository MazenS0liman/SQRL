FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including Playwright dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN --mount=type=ssh,id=default \
    pip install --default-timeout=1000 --retries 5 -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000

CMD ["python", "run.py"]