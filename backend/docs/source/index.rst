Squirrel Documentation
======================

Squirrel is a full-stack data workspace for uploading or connecting tabular
data, preparing it for machine learning, and serving predictions through an
API.

Architecture
------------

The application is composed of a React client and a FastAPI backend. The
backend stores application metadata in PostgreSQL and uploaded files,
processed datasets, pipelines, and model artifacts in MinIO.

The production Docker image builds the frontend first, then serves the
compiled single-page application and the API from the same container.

Quick start
-----------

From the repository root, create ``.env`` from ``.env.example``, add the
provider credentials required by the agents you plan to use, and start the
services:

.. code-block:: powershell

   Copy-Item .env.example .env
   docker compose up --build -d

Open the application at ``http://localhost:8000``. FastAPI's interactive API
reference is available at ``http://localhost:8000/docs`` outside production.

Workflow
--------

1. Register or log in.
2. Create a workspace.
3. Upload CSV data or configure a PostgreSQL connector.
4. Inspect and preprocess the data with the assisted workflow.
5. Train and compare models.
6. Review metrics and use the prediction form or workspace prediction API.

API authentication
------------------

Protected API requests use a bearer token returned by
``/api/auth/login`` or ``/api/auth/register``. A completed workspace can
score rows with ``POST /api/workspace/{workspace_id}/api/predict``.

.. toctree::
   :maxdepth: 3
   :caption: Python API Reference:

   api

