Try It On Your Computer
=======================

This page runs a small project on a Kubernetes cluster on your own
computer, with `kind <https://kind.sigs.k8s.io/>`_ (Kubernetes in Docker)
and a stand-in S3 server inside the cluster. It is the same run the
project's end-to-end test makes on every pull request, so it is known to
work. It takes about ten minutes.

You need Docker, ``kind``, ``kubectl`` and `uv <https://docs.astral.sh/uv/>`_,
and a clone of the repository:

.. code-block:: bash

   git clone https://github.com/jmineau/pystiltctl.git
   cd pystiltctl
   uv sync --extra s3

The project is ``tests/kubernetes/demo``: two receptors at the University
of Utah early on 2021-01-15, three hours back, 100 particles each, with its
results on ``s3://stilt/output``.

Meteorology
-----------

The pods read the met from a folder your computer shares with the cluster.
Download the one HRRR block the project needs, cropped to Utah (about
21 MB once cropped; the download itself is larger):

.. code-block:: bash

   uv run python -c '
   from arlmet.archives import HRRRArchive
   HRRRArchive().fetch("2021-01-15 00:00", "2021-01-15 05:59", dest_dir="met",
                       bbox=(-114.0, 39.0, -110.0, 42.0))
   '

The cluster
-----------

Make a cluster whose node sees that folder at ``/met``:

.. code-block:: bash

   cat > kind.yaml <<EOF
   kind: Cluster
   apiVersion: kind.x-k8s.io/v1alpha4
   nodes:
     - role: control-plane
       extraMounts:
         - hostPath: $PWD/met
           containerPath: /met
           readOnly: true
   EOF
   kind create cluster --name stilt --config kind.yaml

Build the image and give it to the cluster:

.. code-block:: bash

   docker build -t pystiltctl:dev .
   kind load docker-image pystiltctl:dev --name stilt

Start the S3 server. ``tests/kubernetes/cluster.yaml`` also makes the
Secret that tells the pods where it is (``AWS_ENDPOINT_URL``) and a
PersistentVolumeClaim, ``met``, for the met folder:

.. code-block:: bash

   kubectl apply -f tests/kubernetes/cluster.yaml
   kubectl wait --for=condition=available deployment/s3 --timeout=300s
   kubectl port-forward service/s3 5000:5000 &

Run the project
---------------

Point your own shell at the same S3 server, through the port forward:

.. code-block:: bash

   export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
   export AWS_DEFAULT_REGION=us-east-1 AWS_ENDPOINT_URL=http://127.0.0.1:5000
   uv run python -c "import s3fs; s3fs.S3FileSystem().mkdir('stilt')"

Then push, submit and check, as on a real cluster:

.. code-block:: bash

   uv run pystiltctl push tests/kubernetes/demo s3://stilt/projects/demo
   uv run pystiltctl submit s3://stilt/projects/demo --tasks 2 \
       --image pystiltctl:dev --secret bucket-creds --volume met:/met
   kubectl get pods --watch                     # Ctrl-C when both are Completed

``pystiltctl status`` reads the met's header to find the results, so it
needs the met at ``/met`` on your computer too, as in the pods:

.. code-block:: bash

   sudo ln -s "$PWD/met" /met
   uv run pystiltctl status s3://stilt/projects/demo

.. code-block:: text

   Project: s3://stilt/projects/demo
   Output: s3://stilt/output
   Simulations:  total=2  complete=2  failed=0  interrupted=0  pending=0
   Jobs:
     stilt-demo-l2zv5  complete  2 of 2 tasks done, 0 running  started 2026-10-29T17:37:48Z

``kubectl logs`` on a pod shows its ``stilt run``. Submit again and the
new Job finishes at once: both simulations are already complete. The
results are ordinary PYSTILT output on the bucket, under
``s3://stilt/output``.

When you are done:

.. code-block:: bash

   kind delete cluster --name stilt
   sudo rm /met

On a real cluster
-----------------

Three things change. The image is ``ghcr.io/jmineau/pystiltctl``, the
default, so leave out ``--image``. The bucket is a real one, so leave
``AWS_ENDPOINT_URL`` unset and give the pods the bucket's credentials:
a Secret with ``--secret``, or a service account with
``--service-account``. And the met is either downloaded by each pod
(``download: hrrr`` in ``config.yaml``) or a volume the cluster already
has (``--volume``).

.. note::

   ``AWS_ENDPOINT_URL`` sends every S3 request to that server, including
   ``download: hrrr``'s requests to NOAA's bucket. With an S3-compatible
   store such as MinIO, give the pods their met on a volume.
