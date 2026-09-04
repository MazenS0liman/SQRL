FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including Playwright dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn squirrel.api.api:app --host 0.0.0.0 --port ${PORT:-8000}"]