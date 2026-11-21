pystiltctl
==========

Run PYSTILT projects on Kubernetes, with inputs and results on a bucket.

.. note::

   pystiltctl has retired (2026-11), and these pages are kept as they were.
   Running a project on Kubernetes is now part of
   `runtraj <https://jmineau.github.io/runtraj/dev/guides/containers.html>`_:
   ``stilt submit`` with ``backend: kubernetes``, ``stilt schedule`` for an
   hourly project, and its guide to
   `setting up Kubernetes <https://jmineau.github.io/runtraj/dev/guides/kubernetes_setup.html>`_.
   The footprint maps are PYSTILT's map stage, and the image is built from
   PYSTILT's ``Dockerfile``. The `README <https://github.com/jmineau/pystiltctl>`_
   says where each command went.

Release notes are in the `changelog <https://github.com/jmineau/pystiltctl/blob/main/CHANGELOG.md>`_.

.. toctree::
   :maxdepth: 2

   installation
   usage
   kind
   cloud
   cli
   api
   contributing
