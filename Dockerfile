FROM node:20-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./

RUN npm ci

COPY frontend/ ./

ENV VITE_BACKEND_API_BASE_URL=/api

RUN npm run build


FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including Playwright dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY backend/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application
COPY backend/ .

# Serve the compiled frontend from this same application container.
COPY --from=frontend-build /frontend/dist ./frontend-dist

EXPOSE 8000


CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]