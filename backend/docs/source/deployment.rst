Deployment and operations
=========================

Container deployment
--------------------

Build and start the complete stack:

.. code-block:: powershell

   docker compose up --build -d

Check service health and application logs:

.. code-block:: powershell

   docker compose ps
   docker compose logs -f sqrl
   curl http://localhost:8000/api/health

To roll out a new application image, rebuild the ``sqrl`` service and recreate
it. Keep PostgreSQL and MinIO volumes when preserving application data.

Environment management
----------------------

Use a deployment-specific environment file. At minimum, configure the
database, object storage, CORS origins, and encryption keys. Provider
credentials should be scoped to the providers enabled for the deployment.
Never commit ``.env`` files, access keys, or encryption keys.

Production behavior
-------------------

When ``ENVIRONMENT=production``:

* Swagger and ReDoc are disabled by the application.
* The frontend is served from the same origin as the API.
* CORS origins should be explicit; credentialed requests must not use ``*``.
* Logs should be collected from the container and monitored for failed health
  checks and storage errors.

Documentation build
-------------------

The source documentation lives under ``backend/docs/source``. Generate the
HTML site from the backend directory:

.. code-block:: powershell

   .\generate_docs.ps1

The output is written to ``backend/docs/_build/html``. The generator also
refreshes the grouped Python API pages from the backend package.
