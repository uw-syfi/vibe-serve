Use the pre-staged model weights described by the runtime environment; do not
download model weights. Model weights are at `/model` in local environments;
remote environments may require mounting the declared model volume instead.

## Python toolchain

Use `uv` for Python package management. Run `uv init --no-vcs` if `pyproject.toml`
doesn't exist yet, and `uv add` for new dependencies. Always execute Python
scripts via `uv run`.

The independent judge and framework-owned gates apply in addition to this
round's pass criteria. Your implementation must preserve those contracts.

## Use references as implementation support, not as a search policy

The `serving-systems` skill provides technical references. After the active
hypothesis identifies a concrete mechanism, open the router and the smallest
set of references that directly cover that mechanism before editing code. Do
not browse the library for an optimization to try merely because one is
available. In your summary, name the references used and the specific contract
or pitfall they clarified.
