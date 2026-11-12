Architecture
============

SQRL is a browser application backed by a FastAPI service. In production the
compiled React application and API are served by the same container.

.. code-block:: text

   Browser
      |
      |  React SPA / JSON API
      v
   FastAPI application
      |-- authentication and request middleware
      |-- route modules under squirrel.api
      |-- domain services under squirrel.services
      |-- schemas under squirrel.schemas
      |
      +--> PostgreSQL  application records and workspace metadata
      +--> MinIO       files, datasets, pipelines, and model artifacts
      +--> LLM providers and model tooling for assisted workflows

Frontend
--------

The frontend is a React and TypeScript application built with Vite. Its
production assets are emitted to ``dist`` and copied into the backend image.
The browser uses the same-origin ``/api`` prefix in the bundled deployment.

Backend
-------

``main.py`` creates the FastAPI application, registers CORS and request logging
middleware, installs exception handlers, and mounts the API router under
``/api``. In development it exposes Swagger at ``/docs`` and ReDoc at
``/redoc``.

The backend is organized into:

* ``squirrel.api`` — route modules and router registration.
* ``squirrel.schemas`` — request, response, and domain validation models.
* ``squirrel.services`` — authentication, workspace, file, notebook,
  connector, storage, and orchestration behavior.
* ``squirrel.core`` — configuration, middleware, logging, and error handling.
* ``squirrel.models`` — model training and time-series functionality.
* ``squirrel.modules`` — agents, prompts, providers, inspectors, and
  preprocessors used by the assisted workflow.

Storage
-------

PostgreSQL is the system of record for users, workspaces, and metadata.
MinIO provides S3-compatible object storage for files and generated artifacts.
The Compose stack includes health checks so the application starts after both
dependencies are ready.

Deployment boundary
-------------------

The root Dockerfile uses a multi-stage build: Node builds the frontend first,
then Python installs the backend and serves the resulting frontend assets.
The production runtime should provide secrets through the environment and
should keep Swagger and ReDoc disabled unless they are intentionally exposed.
