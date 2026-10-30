Installation
============

pystiltctl requires Python 3.11 or newer.

From GitHub
-----------

.. code-block:: bash

   pip install "pystiltctl[s3] @ git+https://github.com/jmineau/pystiltctl"

The ``s3`` extra reads and writes ``s3://`` buckets (AWS, MinIO, any
S3-compatible store); ``gcs`` does ``gs://`` buckets on Google Cloud.

For development
---------------

Development uses `uv <https://docs.astral.sh/uv/>`_ and `just <https://just.systems/>`_:

.. code-block:: bash

   git clone https://github.com/jmineau/pystiltctl.git
   cd pystiltctl
   uv sync                    # creates .venv with the package and the dev tools
   uv run pre-commit install
   just quality-check

See `CONTRIBUTING.md <https://github.com/jmineau/pystiltctl/blob/main/CONTRIBUTING.md>`_ for the full workflow.
