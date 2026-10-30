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
   inputs.project_name
   inputs.put

Worker
------

.. autosummary::
   :toctree: _autosummary

   worker.run_task
   worker.task_index
   worker.project_directory
   worker.stilt_command
   worker.become
   worker.run

Kubernetes Job
--------------

.. autosummary::
   :toctree: _autosummary

   job.JobSpec
   job.Volume
   job.job_spec
   job.manifest
   job.cron_job
   job.create
   job.apply
   job.to_yaml
   job.default_image
   job.kubernetes_memory
   job.label_value

Status
------

.. autosummary::
   :toctree: _autosummary

   status.simulations
   status.project_jobs
   status.job_summary
   status.report
   status.count_indexes

Schedule
--------

.. autosummary::
   :toctree: _autosummary

   schedule.tick
   schedule.due_hours
   schedule.read_sites
   schedule.hourly_receptors

Images for web maps
-------------------

.. autosummary::
   :toctree: _autosummary

   render.render
   render.image
   render.png
   render.bounds
   render.entry
   render.mercator_y
