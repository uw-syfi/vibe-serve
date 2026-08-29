"""Suite-wide pytest configuration shared by every ``testpaths`` root.

Lives at the repository root rather than under ``tests/`` because the marker it
defines has to mean the same thing for ``tests/``, ``libs/*/tests``, and
``sdk/*/tests``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable

#: xdist scheduling group for tests that cannot run beside one another.
_SERIAL_GROUP = "serial"


def pytest_collection_modifyitems(items: Iterable[pytest.Item]) -> None:
    """Pin every ``serial`` test to one xdist worker.

    CI runs the suite with ``-n auto --dist loadgroup``. A test marked
    ``serial`` claims a process-wide or host-wide resource (a fixed port, a
    fixed path, the process environment), so two of them running at once is a
    flake. ``loadgroup`` sends every item in one ``xdist_group`` to the same
    worker, which serializes them with respect to each other while the rest of
    the suite keeps fanning out. Without xdist the marker is inert, and the
    suite is serial anyway.
    """
    for item in items:
        if item.get_closest_marker(_SERIAL_GROUP) is not None:
            item.add_marker(pytest.mark.xdist_group(_SERIAL_GROUP))
