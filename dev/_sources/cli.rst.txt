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
