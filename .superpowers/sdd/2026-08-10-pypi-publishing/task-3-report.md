# Task 3 implementation report

Status: DONE_WITH_CONCERNS

Commit: db50b03 Build self-contained platform TUI payloads

Implemented:

- Added the four exact wheel targets and host validation.
- Replaced setuptools-time JavaScript work with strict passive TUI payload validation and staging.
- Added a deterministic release builder using pnpm's frozen shared lock, a target-only OpenTUI deployment, Bun 1.3.9, license staging, manifest hashes, and an exact platform wheel suffix check.
- Added a packaged Bun-only CLI launcher with automatic package installation disabled.
- Added an OpenTUI native self-test.
- Added a platform-aware bdist_wheel command and fixed namespace package discovery for prompt data directories.
- Added direct script invocation and CLI contract tests.

Verification:

- `.venv/bin/pytest tests/test_wheel_targets.py tests/test_tui_packaging.py tests/test_cli_launcher.py tests/test_release_wheel_builder.py tests/test_distribution_packaging.py -q --no-cov`: 30 passed.
- Focused Ruff: passed.
- Focused Pyright for production modules: 0 errors.
- Bun 1.3.9 `pnpm --dir clients/tui check`: passed.
- Bun 1.3.9 `pnpm --dir clients/tui test`: 158 passed, 923 assertions.
- Bun 1.3.9 `pnpm --dir clients/tui build`: passed.
- Bun 1.3.9 `clients/tui/dist/self-test.js`: exited 0.
- Real Linux x86_64 build: produced `vibesys-0.1.0-py3-none-manylinux_2_28_x86_64.whl`, 50,656,184 compressed bytes, 1,041 entries, `Root-Is-Purelib: false`, exact target tag, Bun mode 0755, no source maps, no `.bin`, and no `repos/` paths.
- Rebuilt after switching to setuptools' integrated bdist_wheel command: passed without the wheel deprecation or namespace-package warnings.

Concern and plan adjustment:

- Vitest 3.2.7 is not compatible with this Bun/OpenTUI runtime path. Its worker uses an unsupported `MessagePort.addListener`, and worker execution lacks Bun FFI. The same 158 tests now import `bun:test` and run directly under pinned Bun 1.3.9 with concurrency 1. This preserves the test behavior and tests the shipped runtime more directly, but differs from the plan's named Vitest implementation.

## Fix round 1

Status: DONE

Commit: `Fix Task 3 release review findings` (this commit)

Changed files:

- `scripts/build_release_wheel.py`: restored the three-positional-argument public API, made repository-root injection private and optional, and normalized caller-relative Bun and output paths at the API boundary.
- `packaging_support.py`: explicitly excluded build and cache artifact directories from namespace package discovery.
- `third_party/bun/LICENSE`, `MANIFEST.in`: aligned the committed Bun license path and sdist manifest with the plan.
- `tests/test_release_wheel_builder.py`, `tests/test_distribution_packaging.py`: added API, relative-path, license-path, and artifact-exclusion regressions and updated builder call sites.

Red evidence:

- `.venv/bin/pytest tests/test_release_wheel_builder.py::test_build_release_wheel_preserves_the_public_positional_interface -q --no-cov`: failed because the first three parameters were `repo_root`, `target_key`, and `bun`.
- `.venv/bin/pytest tests/test_release_wheel_builder.py::test_build_release_wheel_resolves_caller_relative_paths -q --no-cov`: failed with `Expected exactly one wheel in release, found 0` after the fake `uv` process interpreted `release` under the repository root.
- `.venv/bin/pytest tests/test_distribution_packaging.py::test_namespace_discovery_excludes_build_and_cache_artifacts -q --no-cov`: failed because `example.__pycache__`, `example.build`, `example.dist`, and their children were discovered as packages.
- `.venv/bin/pytest tests/test_release_wheel_builder.py::test_build_release_wheel_assembles_payload_without_mutating_node_modules -q --no-cov`: failed because the consumer still required `third_party/bun/LICENSE.md` after the fixture adopted the planned `LICENSE` path.

Green evidence:

- Each red command passed after its minimal production change (`1 passed` each).
- `.venv/bin/pytest tests/test_wheel_targets.py tests/test_tui_packaging.py tests/test_cli_launcher.py tests/test_release_wheel_builder.py tests/test_distribution_packaging.py -q --no-cov`: `34 passed in 1.23s`.
- `.venv/bin/ruff check scripts/build_release_wheel.py packaging_support.py tests/test_release_wheel_builder.py tests/test_distribution_packaging.py`: `All checks passed!`.
- `.venv/bin/ruff format --check scripts/build_release_wheel.py packaging_support.py tests/test_release_wheel_builder.py tests/test_distribution_packaging.py`: `4 files already formatted`.
- `.venv/bin/pyright scripts/build_release_wheel.py packaging_support.py`: `0 errors, 0 warnings, 0 informations`.
- `git diff --check`: passed.
- Independent read-only review: no Critical, Important, or Minor findings; ready to commit.
