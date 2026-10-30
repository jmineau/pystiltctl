Command line
============

``pystiltctl --help`` lists the commands, and ``pystiltctl <command>
--help`` describes each one.

pystiltctl push
---------------

.. code-block:: text

   pystiltctl push PROJECT URL

Copy a project's inputs (``config.yaml``, ``receptors.csv``, ``tables/``)
to ``URL``, for pods to read. Files already there are replaced.

pystiltctl submit
-----------------

.. code-block:: text

   pystiltctl submit PROJECT [--tasks N] [--parallelism P] [--cpus C] [--memory M]
                     [--image IMAGE] [--output URL] [--secret NAME]... [--service-account NAME]
                     [--volume CLAIM:/path]... [--namespace NS] [--dry-run]

Run a project on Kubernetes as an indexed Job of ``N`` pods, each running
``pystiltctl worker``. ``PROJECT`` is the URL its inputs were pushed to,
or a folder at the same path here and in the pods.

``--tasks``, ``--parallelism``, ``--cpus``, ``--memory``
   Default to ``n_workers``, ``array_parallelism`` (or all tasks),
   ``cpus`` and ``mem`` in the ``execution`` section of ``config.yaml``.
``--image``
   The container image. Defaults to this version's,
   ``ghcr.io/jmineau/pystiltctl:<version>``, or ``:main`` for a
   development version.
``--output``
   The output directory, in place of the one ``config.yaml`` names. A
   project on a bucket needs an output on a bucket.
``--secret``
   A Secret whose keys become the pods' environment variables. Repeat
   for more.
``--service-account``
   The pods' service account.
``--volume``
   A PersistentVolumeClaim to mount, as ``CLAIM:/path``. Repeat for more.
``--namespace``
   Defaults to ``kubectl``'s current namespace.
``--dry-run``
   Print the Job as YAML instead of creating it.

pystiltctl schedule
-------------------

.. code-block:: text

   pystiltctl schedule PROJECT [--delay HOURS] [--render URL] [--cpus C] [--memory M] [--image IMAGE]
                       [--output URL] [--secret NAME]... [--service-account NAME]
                       [--volume CLAIM:/path]... [--namespace NS] [--dry-run]

Run a project every hour for the sites in its ``sites.csv``: a CronJob
whose pod runs ``pystiltctl tick`` at 15 past each hour, one at a time.
The other options are ``submit``'s. ``--delay`` (8) is how many hours
behind the clock it runs, so the met is there. ``--render`` draws each
footprint for a web map at ``URL`` (see the usage page).

pystiltctl tick
---------------

.. code-block:: text

   pystiltctl tick PROJECT [--delay HOURS] [--now TIME] [--render URL] [--cpus C] [--output URL] [--workdir DIR]

What each hourly pod runs: add a receptor per site for each hour of the
last day that is ``--delay`` hours old, write ``receptors.csv`` back,
then ``stilt run --receptors`` on that day. ``--now`` (UTC, such as
``2026-10-01T00:00``) acts as if it were that time, to fill in a past day.
``--render`` then draws the day's finished footprints. Exits with ``stilt
run``'s code.

pystiltctl status
-----------------

.. code-block:: text

   pystiltctl status PROJECT [--output URL] [--namespace NS] [--json]

Count a project's simulations by state (``stilt status``), and list the
Jobs ``pystiltctl submit`` started for it, with their tasks done,
running, and failed. Without ``kubectl``, or a cluster, it counts the
simulations only.

pystiltctl worker
-----------------

.. code-block:: text

   pystiltctl worker PROJECT --tasks N [--index I] [--cpus C] [--output URL] [--workdir DIR]

Run task ``I`` of ``N`` here: copy the inputs from ``PROJECT`` (a URL, or
a folder on a mounted volume), then run ``stilt run --task I/N``.

``--tasks``
   How many tasks the project is split into: the Job's ``completions``.
``--index``
   This task's index, 0 to ``N - 1``. Defaults to ``$JOB_COMPLETION_INDEX``.
``--cpus``
   Receptors to run at once. Defaults to ``execution.cpus`` in ``config.yaml``.
``--output``
   The output directory, in place of the one ``config.yaml`` names.
``--workdir``
   Where a URL's inputs are copied. Defaults to ``/tmp/pystiltctl``.
