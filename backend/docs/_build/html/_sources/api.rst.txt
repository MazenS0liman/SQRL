Backend API Reference
=====================

The SQRL backend reference documents the Python modules that power the
application, including its HTTP routes, validation schemas, domain services,
storage adapters, and machine-learning components.

Use the sections below to navigate by responsibility. Related modules are
combined into category pages—for example, all prompt generators are presented
together under **Prompt Components** instead of appearing as separate pages with
long fully qualified titles. Route modules describe the application boundary,
service modules contain business operations, and supporting modules provide the
shared infrastructure used across the platform.
The pages are generated directly from the backend source and include available
classes, methods, attributes, signatures, and inheritance relationships.

For endpoint-oriented testing and request examples, run the application and
open the interactive OpenAPI reference at ``http://localhost:8000/docs``.


Browse the generated backend reference by responsibility rather than by file.


HTTP API
--------

.. toctree::
   :maxdepth: 1

   generated/api_reference

Data Schemas
------------

.. toctree::
   :maxdepth: 1

   generated/schemas_reference

Services
--------

.. toctree::
   :maxdepth: 1

   generated/services_reference

Agents
------

.. toctree::
   :maxdepth: 1

   generated/agents_reference

Prompt Components
-----------------

.. toctree::
   :maxdepth: 1

   generated/prompts_reference

Model Providers
---------------

.. toctree::
   :maxdepth: 1

   generated/providers_reference

Data Inspectors
---------------

.. toctree::
   :maxdepth: 1

   generated/inspectors_reference

Preprocessors
-------------

.. toctree::
   :maxdepth: 1

   generated/preprocessors_reference

Core Infrastructure
-------------------

.. toctree::
   :maxdepth: 1

   generated/core_reference

Machine Learning Models
-----------------------

.. toctree::
   :maxdepth: 1

   generated/models_reference

Constants
---------

.. toctree::
   :maxdepth: 1

   generated/constants_reference
