Usage
=====

pystiltctl runs a PYSTILT project on a Kubernetes cluster. Each pod runs
one share of the receptors, ``stilt run --task I/N``, and writes its
results straight to a bucket. PYSTILT decides what is finished from those
files, so running a project again only fills in what is missing.
:doc:`kind` runs a small project this way on your own computer.

The container image
-------------------

Every pod runs the pystiltctl image. It has PYSTILT (with HYSPLIT),
pystiltctl, and what ``s3://`` and ``gs://`` buckets need:

.. code-block:: bash

   docker pull ghcr.io/jmineau/pystiltctl:latest   # the latest release
   docker pull ghcr.io/jmineau/pystiltctl:main     # the latest code on main

To build it yourself, from a clone of the repository:

.. code-block:: bash

   docker build --build-arg VERSION=0.1.0 -t pystiltctl .

Put the project on a bucket
---------------------------

Pods cannot see your computer, so the project's inputs go to a bucket
first. ``pystiltctl push`` copies ``config.yaml``, ``receptors.csv`` and
``tables/``:

.. code-block:: bash

   pystiltctl push ./my_project s3://my-bucket/projects/my_project

The output directory is not copied. Name a bucket as the output, so
every pod writes to the same place:

.. code-block:: yaml

   # config.yaml
   output: s3://my-bucket/output

The met ``directory`` in ``config.yaml`` must be a path inside the pod.
With ``download: hrrr`` (see PYSTILT's meteorology guide), each pod
downloads the files its receptors need from NOAA's bucket into that
folder, so nothing has to be staged.

Any URL fsspec knows works: ``s3://`` needs ``pystiltctl[s3]`` (AWS,
MinIO, any S3-compatible store) and ``gs://`` needs ``pystiltctl[gcs]``.
A plain folder works too, when the pods mount it as a volume.

Run it
------

.. code-block:: bash

   pystiltctl submit s3://my-bucket/projects/my_project --secret bucket-creds

``pystiltctl submit`` starts one Kubernetes indexed Job on the cluster
``kubectl`` points at. Its pods split the receptors among them, as the
tasks of a Slurm job array do. What it does not get from options it takes
from the ``execution`` section of ``config.yaml``, so a project set up for
Slurm runs the same way here:

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Option
     - Default
     - What it sets
   * - ``--tasks``
     - ``n_workers``
     - How many tasks (pods in all) the receptors are split into.
   * - ``--parallelism``
     - ``array_parallelism``, or all
     - Most pods running at once.
   * - ``--cpus``
     - ``cpus``
     - Receptors each pod runs at once, and the CPUs it asks for.
   * - ``--memory``
     - ``mem`` (``8G`` becomes ``8Gi``)
     - Memory each pod asks for.

The pods need to reach the bucket. ``--secret`` names a Kubernetes Secret
whose keys become their environment, such as ``AWS_ACCESS_KEY_ID`` and
``AWS_SECRET_ACCESS_KEY`` (and ``AWS_ENDPOINT_URL`` for MinIO).
``--service-account`` gives them a service account instead, for a cluster
that grants bucket access through one (GKE Workload Identity, EKS IAM
roles). ``--volume CLAIM:/path`` mounts a PersistentVolumeClaim, such as
a met archive the ``mets`` directory points into.

``--dry-run`` prints the Job instead of starting it. Save it, change what
the options do not reach (a node selector for spot nodes, tolerations),
and start it with ``kubectl create -f job.yaml``.

Kubernetes retries a task from the worker's exit code: a stopped task
(3) up to three times, and a pod taken back with its node (a spot node)
as often as it happens. A task whose simulations failed (1) is not
retried, since it would fail the same way; nor is anything after a wrong
command line (2), which stops the whole Job. Fix the cause and submit
again: finished simulations are skipped, so only the rest run. The Job,
and its pods' logs, stay on the cluster for a week.

Check on it
-----------

.. code-block:: bash

   pystiltctl status s3://my-bucket/projects/my_project

.. code-block:: text

   Project: s3://my-bucket/projects/my_project
   Output: s3://my-bucket/output
   Simulations:  total=2000  complete=1874  failed=6  interrupted=0  pending=120
     failed: met_missing 6
   Jobs:
     stilt-my-project-x7k2p  running  14 of 20 tasks done, 5 running, 1 failed (tasks 7)  started 2026-10-29T16:00:00Z

The simulation counts are ``stilt status``'s, read from the output
directory, so they are right whatever the Jobs did. ``failed`` counts
failed simulations by reason; each one's ``.failure.yaml``, under
``logs/`` in the output directory, says why. The Jobs are the ones
``pystiltctl submit`` started for this project (by the
``pystiltctl/project`` label), with how many of their tasks are done,
running, and failed. A failed task is one whose simulations failed:
``kubectl logs`` on its pod shows which. ``--json`` prints the same for a
program to read.

A project on a bucket needs its output on a bucket too, in
``config.yaml`` or as ``--output``; give ``status`` the same ``--output``
you gave ``submit``. With a path, ``status`` exits with 2 and says so, as
``submit`` does, instead of counting an empty folder here.

``stilt status`` reads the header of the first met file to find the
results, so it needs the met where you run it, at the path
``config.yaml`` names. A met that the pods download (``download: hrrr``)
needs nothing. A met on a volume must be at the same path on your
computer too.

What each pod runs
------------------

.. code-block:: bash

   pystiltctl worker s3://my-bucket/projects/my_project --tasks 20

``pystiltctl worker`` copies the project's inputs to the pod's disk, then
becomes ``stilt run <project> --task I/20``. ``I`` is the pod's index,
which a Kubernetes indexed Job sets in ``JOB_COMPLETION_INDEX``
(``--index`` sets it by hand). A project on a mounted volume is used where
it is.

The worker exits with ``stilt run``'s code, so Kubernetes can decide what
to retry:

.. list-table::
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - 0
     - Every simulation of the task is complete.
   * - 1
     - Some simulations failed. ``stilt status`` counts them by reason. A
       retry helps only once the cause is fixed.
   * - 2
     - The command line was wrong, or there is no project at the URL.
       Nothing ran. Do not retry.
   * - 3
     - Some simulations were stopped, or the inputs could not be copied.
       A retry continues where it stopped.
