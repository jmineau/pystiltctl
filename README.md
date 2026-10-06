# pystiltctl

Run [PYSTILT](https://github.com/jmineau/PYSTILT) projects at scale outside Slurm: Kubernetes
Jobs, queues, autoscaled workers, and outputs on an object store.

PYSTILT runs a project's receptors on one machine or as a Slurm job array, and each task of an
array is one command line, `stilt run <project> --task I/N`. pystiltctl starts those command
lines elsewhere and keeps the project and its outputs in sync. It calls PYSTILT; PYSTILT knows
nothing of it. [AGENTS.md](AGENTS.md) draws the line between the two.

The layout follows Ben Fasoli's [stiltctl](https://github.com/uataq/stiltctl), which ran STILT-R
on Kubernetes. This is a new codebase that runs PYSTILT.

**Status:** started. Nothing runs yet.

## License

MIT. See [LICENSE](LICENSE).
