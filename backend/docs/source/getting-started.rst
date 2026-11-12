Getting started
===============

This guide gets a local SQRL environment running with the same service layout
used by the containerized application.

Prerequisites
-------------

For the recommended Docker workflow, install Docker Desktop with Compose
support. For split local development, install Python 3.10+, Node.js 20+, npm,
PostgreSQL, and MinIO.

Run with Docker Compose
-----------------------

From the repository root:

.. code-block:: powershell

   Copy-Item .env.example .env
   docker compose up --build -d

The stack exposes these services:

.. list-table::
   :header-rows: 1
   :widths: 28 42

   * - Service
     - Address
   * - SQRL application
     - http://localhost:8000
   * - Interactive API docs
     - http://localhost:8000/docs
   * - PostgreSQL
     - localhost:5433
   * - MinIO API
     - http://localhost:9000
   * - MinIO console
     - http://localhost:9001
   * - pgAdmin
     - http://localhost:5050

Check the application and follow logs:

.. code-block:: powershell

   curl http://localhost:8000/api/health
   docker compose logs -f sqrl

Stop the stack with ``docker compose down``. Use
``docker compose down -v`` only when you intentionally want to remove the
local PostgreSQL and MinIO volumes.

Run the backend locally
-----------------------

.. code-block:: powershell

   Push-Location backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   uvicorn main:app --reload --port 8000
   Pop-Location

The backend expects PostgreSQL and MinIO to be available. The API is mounted
under ``/api`` and the development documentation is available at ``/docs``.

Run the frontend locally
------------------------

.. code-block:: powershell

   Push-Location frontend
   npm ci
   npm run dev
   Pop-Location

The production client uses the same-origin ``/api`` base. During local
development, use the Vite proxy configuration or point the client at the
running backend.

First request
-------------

1. Register at ``POST /api/auth/register``.
2. Log in at ``POST /api/auth/login`` and keep the bearer token.
3. Create a workspace and attach a data source.
4. Inspect and preprocess the data.
5. Train a model and use the recommended model for predictions.

Configuration
-------------

Copy ``.env.example`` before starting the application. Important settings
include ``DATABASE_URL``, ``MINIO_ENDPOINT``, ``MINIO_BUCKET``,
``TOKEN_ENCRYPTION_KEY``, ``SQUIRREL_CONNECTOR_SECRET_KEY``, and
``CORS_ORIGINS``. Provider credentials are required only for the providers
used by the assisted workflow.
