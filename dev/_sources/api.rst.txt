API reference
=============

pystiltctl is used from its command line (:doc:`cli`); these are the
functions behind it.

.. currentmodule:: pystiltctl

Project inputs
--------------

.. autosummary::
   :toctree: _autosummary

   inputs.push
   inputs.pull
   inputs.is_local
   inputs.local_path

Worker
------

.. autosummary::
   :toctree: _autosummary

   worker.run_task
   worker.task_index
   worker.project_directory
   worker.stilt_command

Kubernetes Job
--------------

.. autosummary::
   :toctree: _autosummary

   job.JobSpec
   job.Volume
   job.job_spec
   job.manifest
   job.create
   job.to_yaml
   job.default_image
   job.kubernetes_memory
   job.label_value
