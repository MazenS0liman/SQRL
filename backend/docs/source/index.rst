SQRL Documentation
===================

.. raw:: html

   <section class="sqrl-hero">
     <div class="sqrl-hero-copy">
       <p class="sqrl-eyebrow">DATA WORKSPACE · MACHINE LEARNING · API</p>
       <h1>From raw data to <em>reliable predictions.</em></h1>
       <p class="sqrl-lead">SQRL is a full-stack workspace for connecting data, exploring datasets, building models, and serving predictions through a secure FastAPI backend.</p>
       <div class="sqrl-actions">
         <a class="sqrl-button sqrl-button-primary" href="#get-started">Get started</a>
         <a class="sqrl-button sqrl-button-secondary" href="api.html">Explore the API</a>
       </div>
     </div>
     <div class="sqrl-hero-panel">
       <span class="sqrl-panel-label">WORKSPACE FLOW</span>
       <div class="sqrl-flow-step"><b>01</b><span>Connect data</span><em>CSV · PostgreSQL</em></div>
       <div class="sqrl-flow-step"><b>02</b><span>Prepare &amp; inspect</span><em>Assisted workflow</em></div>
       <div class="sqrl-flow-step"><b>03</b><span>Train &amp; compare</span><em>Fitted pipelines</em></div>
       <div class="sqrl-flow-step"><b>04</b><span>Serve predictions</span><em>Authenticated API</em></div>
     </div>
   </section>

   <section class="sqrl-stats">
     <div class="sqrl-stat"><strong>FastAPI</strong><span>Secure, typed backend</span></div>
     <div class="sqrl-stat"><strong>React</strong><span>Interactive web client</span></div>
     <div class="sqrl-stat"><strong>PostgreSQL + MinIO</strong><span>Records and model objects</span></div>
     <div class="sqrl-stat"><strong>Docker Compose</strong><span>One-command local stack</span></div>
   </section>

Welcome to the SQRL developer hub. This documentation covers local development,
deployment, the application architecture, and the generated Python API.

.. raw:: html

   <p class="sqrl-section-title">Explore the docs</p>
   <section class="sqrl-card-grid">
     <a class="sqrl-card" href="getting-started.html">
       <span class="sqrl-card-icon">01</span>
       <h3>Get started</h3>
       <p>Run the complete stack with Docker Compose or start the backend and frontend independently.</p>
       <strong>Installation guide →</strong>
     </a>
     <a class="sqrl-card" href="workflow.html">
       <span class="sqrl-card-icon">02</span>
       <h3>Understand the workflow</h3>
       <p>Follow a workspace from data ingestion through inspection, training, evaluation, and prediction.</p>
       <strong>Product workflow →</strong>
     </a>
     <a class="sqrl-card" href="architecture.html">
       <span class="sqrl-card-icon">03</span>
       <h3>Explore the architecture</h3>
       <p>See how the React client, FastAPI service, PostgreSQL, MinIO, and model pipeline fit together.</p>
       <strong>System overview →</strong>
     </a>
     <a class="sqrl-card" href="api.html">
       <span class="sqrl-card-icon">04</span>
       <h3>Use the API reference</h3>
       <p>Browse route modules, schemas, services, and supporting classes generated from the backend source.</p>
       <strong>Python API →</strong>
     </a>
   </section>

.. _get-started:

Get started
-----------

The fastest way to run SQRL locally is Docker Compose:

.. raw:: html

   <div class="sqrl-terminal">
     <div class="sqrl-terminal-bar"><i></i><i></i><i></i><span>PowerShell</span></div>
     <pre><span class="p">PS&gt;</span> Copy-Item .env.example .env
   <span class="p">PS&gt;</span> docker compose up --build -d
   <span class="c"># then open http://localhost:8000</span></pre>
   </div>

Then open ``http://localhost:8000``. The interactive OpenAPI reference is
available at ``http://localhost:8000/docs`` in non-production environments.
For a step-by-step setup, see :doc:`getting-started`.

What SQRL provides
------------------

.. raw:: html

   <section class="sqrl-feature-grid">
     <div class="sqrl-feature"><h4>Workspaces</h4><p>Isolate datasets, connections, pipelines, metrics, and models.</p></div>
     <div class="sqrl-feature"><h4>Data connections</h4><p>Bring in uploaded CSV files and PostgreSQL sources.</p></div>
     <div class="sqrl-feature"><h4>Assisted analysis</h4><p>Get inspection and preprocessing guidance as you go.</p></div>
     <div class="sqrl-feature"><h4>Model training</h4><p>Compare candidate models and retain fitted artifacts.</p></div>
     <div class="sqrl-feature"><h4>Predictions</h4><p>Predict from the UI or through authenticated API routes.</p></div>
     <div class="sqrl-feature"><h4>Storage</h4><p>PostgreSQL for application records, MinIO for objects.</p></div>
   </section>

Security and operational notes
------------------------------

Authentication uses bearer tokens issued by the registration and login routes.
Provider credentials and connector secrets should be supplied through
environment variables and never committed to the repository. Production mode
disables the built-in Swagger and ReDoc pages; expose only the routes required
by your deployment.

Documentation map
-----------------

.. toctree::
   :maxdepth: 2
   :caption: Learn and build

   getting-started
   workflow
   architecture
   deployment

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api