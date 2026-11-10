Set Up a Cluster
================

A real run needs four things: a Kubernetes cluster that ``kubectl`` points
at, a bucket for the project and its results, a way for the pods to reach
the bucket, and met. There is nothing else to set up: pystiltctl keeps no
database and runs no service, so a Job or a CronJob is all it leaves on the
cluster.

This page makes them on Google Cloud. Any cluster works the same way;
`Another cluster`_ says what changes. :doc:`kind` runs the same steps on
your own computer, with no cloud account.

Google Cloud
------------

You need a Google Cloud project with billing, the
`gcloud CLI <https://cloud.google.com/sdk/docs/install>`_, and ``kubectl``
with its Google sign-in plugin:

.. code-block:: bash

   gcloud components install kubectl gke-gcloud-auth-plugin

   export PROJECT=my-project ZONE=us-central1-a
   gcloud config set project $PROJECT
   gcloud services enable container.googleapis.com storage.googleapis.com

The bucket
^^^^^^^^^^

.. code-block:: bash

   gcloud storage buckets create gs://my-stilt --location=us-central1 \
       --uniform-bucket-level-access

The project goes under ``gs://my-stilt/projects/``, and its results under
``gs://my-stilt/output``, so ``config.yaml`` says:

.. code-block:: yaml

   output: gs://my-stilt/output

The cluster
^^^^^^^^^^^

Make a cluster with one small node, which runs Kubernetes' own pods, and a
pool of bigger nodes for the simulations. The pool has no nodes until a Job
asks for them, and goes back to none when the Job is done, so it costs
nothing between runs:

.. code-block:: bash

   gcloud container clusters create stilt --zone=$ZONE \
       --workload-pool=$PROJECT.svc.id.goog \
       --machine-type=e2-standard-2 --num-nodes=1
   gcloud container node-pools create work --cluster=stilt --zone=$ZONE \
       --machine-type=e2-standard-8 --spot \
       --enable-autoscaling --min-nodes=0 --max-nodes=20
   gcloud container clusters get-credentials stilt --zone=$ZONE

``--spot`` makes the pool's nodes Spot VMs, much cheaper, which Google can
take back at any time. A pod lost that way is retried without counting
against its task's retries, so nothing is lost but time; leave ``--spot``
out for nodes that stay. ``--max-nodes`` caps the cost of one run.

A pod asks for ``--cpus`` CPUs, and a node keeps a little of its own, so
a pod that asks for all 8 of an 8-CPU machine never starts. Ask for a bit
less (``--cpus 7``), or for half (``--cpus 4``, two pods to a node).

The pods' access to the bucket
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``--workload-pool`` lets a Kubernetes service account act on the bucket
with no key to keep. Make one and give it the bucket:

.. code-block:: bash

   kubectl create serviceaccount stilt
   NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
   MEMBER=principal://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$PROJECT.svc.id.goog/subject/ns/default/sa/stilt
   gcloud storage buckets add-iam-policy-binding gs://my-stilt \
       --role=roles/storage.objectUser --member=$MEMBER

``roles/storage.objectUser`` reads, writes and deletes files in that bucket
only. Then give ``--service-account stilt`` to ``submit`` and ``schedule``.

Your own computer reads and writes the bucket too (``push``, ``status``).
Sign in once, and install pystiltctl with the ``gcs`` extra:

.. code-block:: bash

   gcloud auth application-default login
   pip install "pystiltctl[gcs] @ git+https://github.com/jmineau/pystiltctl"

Run it
^^^^^^

.. code-block:: bash

   pystiltctl push ./my_project gs://my-stilt/projects/my_project
   pystiltctl submit gs://my-stilt/projects/my_project --service-account stilt
   pystiltctl status gs://my-stilt/projects/my_project

:doc:`usage` has the rest: the options, the retries, and what ``status``
reports.

Met
---

With ``download: hrrr``, each pod downloads the met its receptors need from
NOAA's archive, which is public. Point the met at the pod's own disk,
``/scratch``, which is on the node's boot disk:

.. code-block:: yaml

   mets:
     hrrr:
       download: hrrr
       directory: /scratch/met
       subgrid_enable: true
       subgrid_bounds: {xmin: -114.0, xmax: -110.0, ymin: 39.0, ymax: 42.0}

Each 6-hour block is 3.4 GB before it is cropped, so a pod needs a few
blocks' room; a node's boot disk is 100 GB unless ``--disk-size`` says
otherwise. The met goes away with the pod, so two pods on the same days
each download it.

``schedule`` runs one pod an hour, every hour, so it keeps its met on a
volume instead, and downloads each block once. Make the volume:

.. code-block:: bash

   kubectl apply -f - <<EOF
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: met
   spec:
     accessModes: [ReadWriteOnce]
     resources:
       requests:
         storage: 50Gi
   EOF

and give it to the schedule, with ``directory: /met`` in ``config.yaml``:

.. code-block:: bash

   pystiltctl schedule gs://my-stilt/projects/towers --service-account stilt \
       --volume met:/met

``ReadWriteOnce`` is a disk one node uses at a time. That is enough for
a schedule, whose pods run one after another. Cropped to the box above, a
year of HRRR is about 30 GB.

Footprints on a web map
-----------------------

``tick --render`` writes images a browser reads (:doc:`usage`), so their
bucket is public, and lets your site's address read it:

.. code-block:: bash

   gcloud storage buckets create gs://my-site --location=us-central1 \
       --uniform-bucket-level-access
   gcloud storage buckets add-iam-policy-binding gs://my-site \
       --role=roles/storage.objectViewer --member=allUsers
   gcloud storage buckets add-iam-policy-binding gs://my-site \
       --role=roles/storage.objectUser --member=$MEMBER

   cat > cors.json <<EOF
   [{"origin": ["https://my-site.example"], "method": ["GET"], "maxAgeSeconds": 3600}]
   EOF
   gcloud storage buckets update gs://my-site --cors-file=cors.json

The first binding lets anyone read it; the second lets the pods write to
it. An organization that forbids public buckets refuses the first: ask its
admin, or serve the images another way.

Another cluster
---------------

Any Kubernetes cluster works, with an ``s3://`` bucket on AWS or any
S3-compatible store. Without a workload identity, put the bucket's keys
in a Secret, and give it to ``submit`` and ``schedule`` with ``--secret``;
its keys become the pods' environment:

.. code-block:: bash

   cat > aws.env <<EOF
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_DEFAULT_REGION=us-west-2
   EOF
   kubectl create secret generic bucket-creds --from-env-file=aws.env
   rm aws.env

   pystiltctl submit s3://my-bucket/projects/my_project --secret bucket-creds

On AWS's own clusters (EKS), an IAM role for a service account does what
Workload Identity does above: give that service account with
``--service-account`` instead. A store other than AWS also needs
``AWS_ENDPOINT_URL`` in the Secret; :doc:`kind` says why its met must then
come from a volume.

When you are done
-----------------

Delete the met volume first, so its disk goes with it, then the cluster:

.. code-block:: bash

   kubectl delete pvc met
   gcloud container clusters delete stilt --zone=$ZONE

The bucket stays, with the results. ``gcloud storage rm --recursive
gs://my-stilt`` deletes it and everything in it.
