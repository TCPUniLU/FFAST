# FFAST documentation

[Project overview and quick start](../README.md)

| Guide | Contents |
| --- | --- |
| [Installation](installation.md) | Python, uv, pip and launch options; [local/cluster diagram](installation.md#cluster-sessions) |
| [Using FFAST](usage.md) | Data, analysis, 3D, subsets, sessions and CLI; [loading](usage.md#loading-data) screenshot |
| [LAMMPS](lammps.md) | Live scoring, jobs and alarms; [reference fitting](lammps.md#make-a-reference) and [monitoring flow](lammps.md#wrap-the-launch) diagrams |
| [Cluster setup](clusters.md) | SSH/SLURM profiles and remote connections |
| [Custom metrics and tabs](custom-metrics.md) | Dataset fields, formulas, panels and Python metrics; screenshots for [formulas](custom-metrics.md#your-own-metrics-in-the-browser) and [tab editing](custom-metrics.md#panels-and-tabs) |
| [Scripting and configuration](scripting.md) | Python without a UI; configuration files |
| [Writing a reader](writing-a-reader.md) | Load and export a file format FFAST does not know: a small Python class, with examples |
| [Example datasets](../examples/data/README.md) | Sources, permissions, predictions and units |
| [LAMMPS examples](../examples/lammps/README.md) | Replay assets and tutorial data |
| [Troubleshooting](troubleshooting.md) | Browser connection flowchart, installation, and data problems |
| [Performance profiling](performance-profiling.md) | Repeatable full-file loading and Basic Errors profiles, resource samples and cache comparisons |

For development, start with [Contributing](../CONTRIBUTING.md), the
[architecture](architecture.md), [domain vocabulary](../CONTEXT.md),
[design decisions](adr/README.md) and the [UI design brief](ui-design-brief.md)
(proposed refinements to [DESIGN.md](../DESIGN.md)). Research notes, implementation plans and
handoffs record development work; they are not usage instructions.
