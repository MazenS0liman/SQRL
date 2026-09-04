# Squirrel

Squirrel is a full-stack data workspace for uploading or connecting tabular data, preprocessing it, training and comparing machine-learning models, and exposing trained models through an API.

The production-style Docker setup runs the React frontend and FastAPI backend in one application container. PostgreSQL stores application data and MinIO stores uploaded files, processed datasets, fitted pipelines, and model artifacts.

## Features

- Workspace-based data science workflow
- CSV uploads and PostgreSQL data connectors
- Multi-source data merging
- LLM-assisted inspection and preprocessing
- Reusable fitted preprocessing pipelines
- Model training, comparison, and artifact downloads
- Prediction on uploaded CSV rows
- External prediction endpoint for trained workspace models
- User authentication and encrypted provider-token storage
- PostgreSQL and MinIO persistence

## Architecture

```text
Browser
	|
	| http://localhost:8000
	v
sqrl container
	|- FastAPI API: /api/*
	|- Compiled React SPA: /*
	|
	+--> PostgreSQL: application records and workspace metadata
	+--> MinIO: files, processed CSVs, pipelines, and models
```

The root `Dockerfile` uses a multi-stage build:

1. Node builds `frontend/dist`.
2. Python installs backend dependencies.
3. FastAPI serves the compiled frontend and API from the same container.

## Requirements

For Docker deployment:

- Docker Desktop with Compose support

For local development:

- Python 3.10 or newer
- Node.js 20 or newer
- npm
- PostgreSQL and MinIO, unless using the Compose services

## Docker Quick Start

Create a local environment file if one does not exist:

```powershell
Copy-Item .env.example .env
```

Add the required provider credentials to `.env`. Do not commit `.env` or real API keys.

Start the stack from the repository root:

```powershell
docker compose up --build -d
```

Open the application at:

```text
http://localhost:8000
```

Useful service URLs:

| Service | URL |
| --- | --- |
| Squirrel | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| PostgreSQL | localhost:5433 |
| MinIO API | http://localhost:9000 |
| MinIO console | http://localhost:9001 |
| pgAdmin | http://localhost:5050 |

View application logs:

```powershell
docker compose logs -f sqrl
```

Stop the stack:

```powershell
docker compose down
```

Stop the stack and delete database/object-storage volumes:

```powershell
docker compose down -v
```

The last command permanently removes local PostgreSQL and MinIO data.

## Environment Configuration

The backend reads settings from `.env` through Docker Compose. Start with `.env.example` and configure at least the provider keys required by the agents you plan to use.

Common variables:

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | Runtime environment, such as `development` or `production` |
| `LOG_LEVEL` | Backend logging level |
| `DATABASE_URL` | Optional database connection override |
| `MINIO_ENDPOINT` | MinIO endpoint used by the backend |
| `MINIO_BUCKET` | Object-storage bucket name |
| `MINIO_ACCESS_KEY` | MinIO access key |
| `MINIO_SECRET_KEY` | MinIO secret key |
| `GEMINI_API_KEY_V1` | Gemini provider credential |
| `GROQ_API_KEY_V1` | Groq provider credential |
| `OPENROUTER_API_KEY_V1` | OpenRouter provider credential |
| `HUGGINGFACE_API_KEY_V1` | Hugging Face provider credential |
| `SQUIRREL_CONNECTOR_SECRET_KEY` | Fernet key for connector secrets |

For the single-container deployment, the frontend uses the relative API base `/api`. This allows the browser to call the backend through the same origin and avoids hard-coded container or host addresses.

## Application Workflow

1. Register or log in at `/signup` or `/login`.
2. Create a workspace at `/workspace`.
3. Upload CSV files or attach a configured PostgreSQL connector.
4. Choose the target column and optionally describe how sources should relate.
5. Build models.
6. Review preprocessing steps, transformed data, model metrics, and recommendations.
7. Use the prediction form to score new CSV rows.
8. Use the external API panel to call the trained model from another application.

## Prediction API

All protected API requests require a bearer token obtained from `/api/auth/login` or `/api/auth/register`.

Register:

```http
POST http://localhost:8000/api/auth/register
Content-Type: application/json
```

```json
{
	"username": "analyst",
	"email": "analyst@example.com",
	"password": "replace-with-a-strong-password",
	"full_name": "Data Analyst"
}
```

Predict with a completed workspace:

```http
POST http://localhost:8000/api/workspace/{workspace_id}/api/predict
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
	"rows": [
		{
			"age": 42,
			"income": 72000,
			"region": "north"
		}
	],
	"model_key": "random_forest"
}
```

Omit `model_key` to use the workspace's recommended model. The rows must contain the raw feature names expected by the workspace. The backend replays the fitted preprocessing pipeline before running the model.

The original authenticated route is also available:

```text
POST /api/workspace/{workspace_id}/predict
```

## Local Development

### Backend

```powershell
Push-Location backend
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
Pop-Location
```

### Frontend

```powershell
Push-Location frontend
npm install
npm run dev
Pop-Location
```

The frontend development server runs at `http://localhost:8080`. Set `VITE_BACKEND_API_BASE_URL` in a local frontend environment file when the backend is not available at the default relative `/api` path:

```text
VITE_BACKEND_API_BASE_URL=http://localhost:8000/api
```

Useful frontend commands:

```powershell
npm run build
npm run lint
```

## Project Layout

```text
.
|- Dockerfile                 # Combined frontend/backend production image
|- docker-compose.yml         # Application, PostgreSQL, MinIO, and pgAdmin
|- backend/
|  |- main.py                 # FastAPI application entry point
|  |- squirrel/api/           # API routers
|  |- squirrel/services/      # Workspace, storage, auth, and connector services
|  |- squirrel/modules/       # Inspectors, preprocessors, agents, and providers
|  |- squirrel/schemas/       # API and domain schemas
|  `- requirements.txt
|- frontend/
|  |- src/                    # React application
|  |- public/imgs/            # Public image assets served at /imgs/*
|  `- package.json
`- .env.example
```

## Troubleshooting

### Requests go to `/undefined/...`

The frontend API base is compiled at build time. Rebuild the image from the repository root:

```powershell
docker compose up --build -d
```

The production value should be `/api`. A hard refresh may be required after replacing an old browser bundle.

### Images do not appear

Public frontend images live in `frontend/public/imgs` and must be referenced as `/imgs/<name>`, not as `./public/imgs/<name>` or `./frontend/public/imgs/<name>`.

### Compose cannot find `backend`

Run Compose from the repository root. The canonical build configuration is:

```yaml
build:
	context: .
	dockerfile: Dockerfile
```

### API returns database or object-storage errors

Check service health and logs:

```powershell
docker compose ps
docker compose logs postgres
docker compose logs minio
docker compose logs sqrl
```

### Port already in use

Change the host-side port in `docker-compose.yml`, for example `"8080:8000"`, then open `http://localhost:8080`. Keep the container-side port at `8000` unless the Dockerfile command and health checks are changed together.

## Security Notes

- Never commit `.env`, provider keys, passwords, or connector credentials.
- Change the example PostgreSQL, MinIO, and pgAdmin passwords before deployment.
- Use a strong `SQUIRREL_CONNECTOR_SECRET_KEY` in non-development environments.
- Restrict CORS origins before exposing the service publicly.
- Put the application behind HTTPS and a reverse proxy for production use.
- Do not expose MinIO or pgAdmin publicly unless required.
