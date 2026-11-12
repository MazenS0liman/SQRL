Product workflow
================

SQRL organizes machine-learning work around a workspace. A workspace keeps
the source data, derived files, preprocessing decisions, model metrics, and
prediction configuration together.

1. Authenticate
----------------

Create an account or sign in. The API returns a bearer token; send it with
protected requests:

.. code-block:: text

   Authorization: Bearer <access-token>

2. Create a workspace
---------------------

Use a workspace as the boundary for a project. It provides a stable identifier
for files, connectors, notebooks, training runs, and prediction requests.

3. Connect data
---------------

Upload CSV files for a self-contained dataset or configure a PostgreSQL
connection for reusable external data. Connector secrets are encrypted when
the corresponding encryption key is configured.

4. Inspect and prepare
----------------------

The assisted workflow examines the dataset, reports useful characteristics,
and recommends preprocessing operations. The resulting transformed data and
pipeline artifacts are retained with the workspace so training and prediction
use the same transformations.

5. Train and compare models
---------------------------

Training evaluates multiple candidate estimators and records their metrics.
The workspace can identify a recommended model while still allowing a caller
to select a specific model key when the API supports it.

6. Predict
----------

Prediction requests contain rows using the original feature names. SQRL
replays the fitted preprocessing pipeline before invoking the selected model:

.. code-block:: json

   {
     "rows": [
       {"age": 42, "income": 72000, "region": "north"}
     ],
     "model_key": "random_forest"
   }

The external prediction route is
``POST /api/workspace/{workspace_id}/api/predict``. Omit ``model_key`` to use
the workspace recommendation when one is available.

Operational boundaries
----------------------

PostgreSQL stores application metadata and relationships. MinIO stores
uploaded files, processed datasets, fitted pipelines, and model artifacts.
Keeping these responsibilities separate allows large objects to be managed
without putting them in relational records.
