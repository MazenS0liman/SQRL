# Squirrel Backend

FastAPI service for the Squirrel data workspace. It provides authentication,
workspace management, data connectors, assisted inspection and preprocessing,
model training, and prediction endpoints.

## Run locally

From this directory, install the dependencies and start the development server:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

The backend expects PostgreSQL and MinIO. The repository root's Compose file
starts both services, maps PostgreSQL to `localhost:5433`, and maps MinIO to
`localhost:9000` with its console at `localhost:9001`.

## Configuration

Copy the repository's `.env.example` to `.env` and configure the storage,
database, connector encryption, and provider credentials needed by your
workflow. Common settings include `DATABASE_URL`, `MINIO_ENDPOINT`,
`MINIO_BUCKET`, and `SQUIRREL_CONNECTOR_SECRET_KEY`.

### Databricks and Airflow pipelines

Set `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, and
`DATABRICKS_TOKEN` to enable the authenticated `/api/warehouse/query`
endpoint. The `/api/warehouse/dags` endpoint generates an Airflow DAG with
extract, transform, and load tasks. Set `AIRFLOW_DAGS_DIR` to a directory
mounted into the Airflow scheduler; Airflow must have the Databricks SQL
connector installed and the same three Databricks variables available.

The generated pipeline uses read-only SQL for extraction and requires the
transform query to reference `source_data`. It writes a staging table, a
transformed table, and finally replaces the requested target table.

## API documentation

When running outside production, interactive OpenAPI documentation is available
at `http://localhost:8000/docs`; the raw schema is at
`http://localhost:8000/openapi.json`.

## Generate documentation

Run the PowerShell helper from this directory:

```powershell
.\generate_docs.ps1
```

The generated HTML is written to `docs/_build/html`.
