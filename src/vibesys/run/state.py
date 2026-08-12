"""Application boundary joining typed project state with Git history."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibesys.run.git_tracker import GitTracker
    from vs_loop_state import RoundRecord
    from vs_project_state import ProjectStore, StateNamespace


class RunStateNamespace(StrEnum):
    """Framework-owned state namespaces below one ``.vs`` run."""

    AGENT = "agent"
    EVOLVE = "evolve"
    PLAIN = "plain"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class RunState:
    """Typed access to one run's portable and machine-local state.

    ``ProjectStore`` owns path resolution and filesystem serialization.
    ``GitTracker`` receives only immutable snapshots produced by that store.
    """

    store: ProjectStore
    git: GitTracker
    run_id: str

    def portable(self, namespace: RunStateNamespace) -> StateNamespace:
        """Return the portable state handle for ``namespace``."""
        return self.store.portable_namespace(self.run_id, namespace.value)

    def local(self, namespace: RunStateNamespace) -> StateNamespace:
        """Return the machine-local state handle for ``namespace``."""
        return self.store.local_namespace(self.run_id, namespace.value)

    def commit(self, label: str, namespace: StateNamespace) -> None:
        """Commit the exact current contents of one portable namespace."""
        self.git.snapshot_framework_state(label, namespace.snapshot())

    def completed_rounds(self) -> list[RoundRecord]:
        """Load the validated completed-round history for this run."""
        return self.store.load_rounds(self.run_id)
