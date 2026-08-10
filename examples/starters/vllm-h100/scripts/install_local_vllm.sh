#!/usr/bin/env bash
set -euo pipefail

# vLLM uses setuptools-scm for versioning, which requires a .git directory.
# workspace.sources clones with strip_git=true, so .git is absent. Set the
# pretend-version env var so setuptools-scm skips the git lookup.
if [ ! -d ./vllm/.git ]; then
  vllm_version=$(python -c "
import tomllib, pathlib, re
# Try the starter's pyproject.toml pin first.
pp = pathlib.Path('pyproject.toml')
if pp.exists():
    deps = tomllib.loads(pp.read_text()).get('project', {}).get('dependencies', [])
    for d in deps:
        m = re.match(r'vllm==([0-9a-z.]+)', d)
        if m:
            print(m.group(1))
            raise SystemExit(0)
# Fallback: read the version constant from vllm source.
vp = pathlib.Path('vllm/vllm/version.py')
if vp.exists():
    for line in vp.read_text().splitlines():
        m = re.match(r'__version__\s*=\s*[\"'\'']([^\"'\'']+)', line)
        if m:
            print(m.group(1))
            raise SystemExit(0)
print('0.0.0')
" 2>/dev/null)
  export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VLLM="$vllm_version"
fi

python -m pip install -e ./vllm
