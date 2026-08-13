#!/usr/bin/env bash
set -euo pipefail

# The installable SGLang package lives under sglang/python (the cloned repo
# root is a monorepo). Build it editable against the pinned source tree.
#
# workspace.sources clones with strip_git=true, so .git is absent. SGLang reads
# its version from python/sglang/version.py (static, no setuptools-scm), so the
# missing .git does not block the editable install here.
if [ ! -d ./sglang/python ]; then
  echo "expected ./sglang/python from the pinned workspace.sources clone" >&2
  exit 1
fi

# Base editable install. The compiled kernels (sgl-kernel, flashinfer) are large
# Blackwell builds; install them explicitly in main.py's Modal image when needed
# rather than as part of this convenience script.
python -m pip install -e ./sglang/python
