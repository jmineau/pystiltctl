# The end-to-end test on kind

`.github/workflows/kubernetes.yml` runs a real project on a kind cluster on
every pull request; `docs/kind.rst` is the same run by hand.

- `demo/`: two receptors (and `sites.csv`, one site, for the schedule step), 3 hours back, 100 particles, met at `/met`, results on
  `s3://stilt/output`. A backward run also needs the hour after its receptor,
  so the receptors end at 04:00: the met is the one 6-hour HRRR block
  `20210115_00-05`, cropped to Utah, which the workflow downloads from NOAA
  and caches.
- `cluster.yaml`: a moto S3 server, the Secret that points the pods at it
  (`AWS_ENDPOINT_URL`), and the met folder as a PersistentVolumeClaim. One
  `List`, so it is one YAML document (the `check-yaml` hook takes no more).

The workflow pushes `demo/`, submits it with the image built from the commit,
waits for the Job, and checks that `pystiltctl status` counts 2 complete
simulations and one complete Job. Then it submits a Job whose project is
missing and checks that it fails with reason `PodFailurePolicy`, without
retries (the worker's exit code 2). Last, it schedules `demo/`, starts one
tick from the CronJob by hand, and checks that the tick added a day of
hours (24 receptors) to the bucket's `receptors.csv` and that its Job failed
by the exit-code rule (`FailedIndexes`): the demo's met is from 2021, so
today's hours cannot run, as when an hour's met has not arrived.

Two things to know:

- `AWS_ENDPOINT_URL` sends every S3 call to moto, NOAA's too, so the pods'
  met must be the mounted folder, never a `download:`.
- `stilt status` reads the met's first file header to find the results, so
  wherever `pystiltctl status` runs, the met must be at the path
  `config.yaml` names: the workflow links the runner's folder to `/met`.
