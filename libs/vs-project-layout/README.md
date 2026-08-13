# vs-project-layout

Typed filesystem boundary for repository-native VibeSys configuration.

The package owns the physical layout below `.vibesys/`. Application code uses
`ProjectLayout` to discover projects and tasks and receives typed capabilities
for authored configuration, generated state, and the evaluator lock. It does
not construct `.vibesys` paths itself.

```text
.vibesys/
├── evaluators.lock
├── tasks/
│   └── <task-name>/
│       ├── OBJECTIVE.md
│       └── vibesys.input.toml
└── state/
```

`tasks/` and its contents are human-authored. `state/` is merely reserved by
this package; `vs-project-state` owns its contents. State need not exist for a
project to be discovered.

Acquire a layout through `ProjectLayout.open()` for an exact existing directory
or `ProjectLayout.discover()` to search that directory and its parents. Call
`initialize()` on an opened layout to create only `.vibesys/tasks`; it does not
create generated state or an evaluator lock.

```python
from vs_project_layout import ProjectLayout

layout = ProjectLayout.discover(".")
task = layout.select_task("hotel-reservation")

project_directory = layout.project_root.path
objective = task.objective_path
state_capability = layout.state_root()
lock_capability = layout.evaluator_lock()
```

`select_task()` may omit the name only when exactly one task exists. Roots and
required files returned by `ProjectLayout` are resolved and checked for
containment. Invalid task names, path traversal, missing required files, and
symlinks that escape their semantic root are rejected before a task is
returned. `StateRootCapability` and `EvaluatorLockCapability` identify paths
owned by other packages; this package does not create or interpret their
contents.
