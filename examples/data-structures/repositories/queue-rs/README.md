# Queue Optimization Repository

This repository-shaped fixture contains a naive Rust bounded queue and three
VibeSys tasks under `.vibesys/tasks`: `spsc`, `mpsc`, and `mpmc`.

Run VibeSys from this directory and select one task. The coding agent works in
the repository root, while the selected task supplies its objective,
correctness gate, and benchmark.

The fixture is stored inside the VibeSys repository, so it cannot include its
own nested `.git` directory. Tests and run setup copy it to an isolated location
and initialize that copy as a standalone Git repository before optimization.
