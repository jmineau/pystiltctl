Usage
=====

pystiltctl runs a PYSTILT project on a Kubernetes cluster. Each pod runs
one share of the receptors, ``stilt run --task I/N``, and writes its
results straight to a bucket. PYSTILT decides what is finished from those
files, so running a project again only fills in what is missing.

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
