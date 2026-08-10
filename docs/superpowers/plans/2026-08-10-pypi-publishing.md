# PyPI Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and publish four verified, self-contained `vibesys` wheels whose one-line installation needs neither the repository nor separately published internal packages or JavaScript runtimes.

**Architecture:** The root distribution explicitly owns the existing framework package roots, stages tracked agent resources and the installable `vs-bench` SDK source, and accepts a prebuilt target-specific TUI payload. A release builder prepares locked frontend dependencies plus pinned Bun, setuptools emits a validated platform wheel, and native CI installs that exact wheel into a clean environment before Trusted Publishing.

**Tech Stack:** Python 3.12, setuptools/wheel, uv, pnpm 11.11.0, Bun 1.3.9, OpenTUI 0.4.3, pytest, Vitest, Docker, GitHub Actions, PyPI Trusted Publishing

## Global Constraints

- Publish one distribution named `vibesys`; never publish `vs-feature-flags`, `vs-github`, `vs-issue-board`, `vs-loop-state`, `vs-sandbox`, or `vs-bench`.
- The `vibesys` distribution provides `vibesys`, `vs_feature_flags`, `vs_github`, `vs_issue_board`, `vs_loop_state`, and `vs_sandbox`.
- Bundle tracked `resources/profilers`, tracked `resources/skills`, and the installable `sdk/vs-bench` project; exclude `.git`, `__pycache__`, `*.pyc`, and `resources/skills/**/repos`.
- Bun is pinned to 1.3.9; use baseline x86_64 archives and disable Bun automatic package installation at runtime.
- Release targets are `py3-none-manylinux_2_28_x86_64`, `py3-none-manylinux_2_28_aarch64`, `py3-none-macosx_13_0_x86_64`, and `py3-none-macosx_13_0_arm64`.
- A release build fails on missing resources, SDK, TUI payload, license, version mismatch, target mismatch, or unexpected wheel contents.
- Editable installs remain headless-capable and do not invoke pnpm, npm, Bun, or Node during setuptools execution.
- The initial PyPI upload contains exactly four wheels and no root sdist.

---

### Task 1: Make the root distribution own internal framework packages

**Files:**
- Create: `packaging_support.py`
- Modify: `setup.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Delete: `libs/vs-feature-flags/pyproject.toml`
- Delete: `libs/vs-github/pyproject.toml`
- Delete: `libs/vs-issue-board/pyproject.toml`
- Delete: `libs/vs-loop-state/pyproject.toml`
- Delete: `libs/vs-sandbox/pyproject.toml`
- Modify: `libs/*/README.md`
- Create: `tests/test_distribution_packaging.py`

**Interfaces:**
- Produces: `discover_distribution_packages(repo_root: Path) -> tuple[list[str], dict[str, str]]`
- Produces: root metadata with no `Requires-Dist: vs-*` entries and direct `mcp>=1.0` plus `modal>=1.4.2` requirements
- Consumes: existing source roots under `src/` and `libs/*/src/`

- [ ] **Step 1: Write package-discovery and metadata tests**

```python
def test_root_distribution_discovers_every_internal_package():
    packages, package_dir = discover_distribution_packages(PROJECT_ROOT)
    assert {"vibesys", "vs_feature_flags", "vs_github", "vs_issue_board", "vs_loop_state", "vs_sandbox"} <= set(packages)
    assert package_dir["vs_sandbox"] == "libs/vs-sandbox/src/vs_sandbox"

def test_root_metadata_has_no_internal_distribution_dependencies(built_wheel):
    requires = wheel_requires_dist(built_wheel)
    assert not {name for name in requires if name.startswith("vs-")}
    assert {"mcp", "modal"} <= requires
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run pytest tests/test_distribution_packaging.py -v`

Expected: import/discovery assertions fail because `packaging_support.py` does not exist and root metadata still depends on five workspace projects.

- [ ] **Step 3: Add explicit multi-root package discovery**

```python
PACKAGE_SOURCE_ROOTS = (
    Path("src"),
    Path("libs/vs-feature-flags/src"),
    Path("libs/vs-github/src"),
    Path("libs/vs-issue-board/src"),
    Path("libs/vs-loop-state/src"),
    Path("libs/vs-sandbox/src"),
)

def discover_distribution_packages(repo_root: Path) -> tuple[list[str], dict[str, str]]:
    names: list[str] = []
    directories: dict[str, str] = {}
    for relative_root in PACKAGE_SOURCE_ROOTS:
        source_root = repo_root / relative_root
        for name in find_packages(where=source_root):
            names.append(name)
            package_path = relative_root / Path(*name.split("."))
            directories[name] = package_path.as_posix()
    return sorted(names), directories
```

Pass both values to `setup(packages=..., package_dir=..., cmdclass=...)`. Keep the helper dependency-free except for setuptools because it runs in the isolated PEP 517 build environment.

- [ ] **Step 4: Consolidate distribution metadata**

Remove the five `vs-*` root requirements and their `[tool.uv.sources]` entries. Add `mcp>=1.0` and `modal>=1.4.2`, retain already-present `deepagents` and `pydantic`, add `py.typed` package data for each internal import package, and change workspace members to `sdk/*` only. Remove independent build metadata under `libs/*`; update those READMEs to state that the modules ship inside `vibesys`.

- [ ] **Step 5: Refresh the lock and run focused verification**

Run:

```bash
uv lock
uv sync --dev
uv run pytest tests/test_distribution_packaging.py libs/vs-feature-flags/tests libs/vs-github/tests libs/vs-issue-board/tests libs/vs-loop-state/tests libs/vs-sandbox/tests -v
```

Expected: all internal imports resolve from the root editable distribution and all focused tests pass.

- [ ] **Step 6: Commit the distribution ownership change**

```bash
git add packaging_support.py setup.py pyproject.toml uv.lock libs tests/test_distribution_packaging.py
git commit -m "Bundle internal Python packages in vibesys"
```

---

### Task 2: Package resources and the input SDK through explicit locators

**Files:**
- Modify: `resources_packaging.py`
- Create: `src/vibesys/sdk_paths.py`
- Modify: `src/vibesys/input_project.py`
- Modify: `setup.py`
- Modify: `pyproject.toml`
- Modify: `MANIFEST.in`
- Modify: `tests/test_resources_packaging.py`
- Modify: `tests/test_input_project.py`
- Create: `tests/test_sdk_packaging.py`

**Interfaces:**
- Produces: `stage_resources(repo_root: Path, dest: Path, *, required: bool = False) -> bool`
- Produces: `stage_sdk(repo_root: Path, dest: Path, *, required: bool = False) -> bool`
- Produces: `sdk_root() -> Path | None`
- Produces: `resolve_sdk_source(project_dir: Path, raw_path: str, *, checkout_sdk_root: Path, packaged_sdk_root: Path | None) -> Path`
- Consumes: Task 1's root wheel ownership and existing `materialize_input_project`

- [ ] **Step 1: Add failing resource and SDK staging tests**

```python
def test_stage_sdk_preserves_installable_project(tmp_path):
    repo = make_repo_with_vs_bench(tmp_path)
    destination = tmp_path / "vibesys" / "_sdk"
    assert stage_sdk(repo, destination, required=True)
    assert (destination / "vs-bench/pyproject.toml").is_file()
    assert (destination / "vs-bench/src/vs_bench/py.typed").is_file()
    assert not (destination / "vs-bench/tests").exists()

def test_required_resource_staging_rejects_missing_tree(tmp_path):
    with pytest.raises(PackagingError, match="resources/skills"):
        stage_resources(tmp_path, tmp_path / "out", required=True)
```

- [ ] **Step 2: Add failing installed-SDK resolution tests**

Exercise checkout preference, packaged fallback for `../../../sdk/vs-bench`, unknown SDK packages, `sdk/../` traversal, absolute paths, and missing `pyproject.toml`. The packaged-fallback test must materialize `_input_libs/vs-bench` from a fake `site-packages/vibesys/_sdk` tree while the checkout root has no `sdk/` directory.

- [ ] **Step 3: Run focused tests and verify expected failures**

Run: `uv run pytest tests/test_resources_packaging.py tests/test_sdk_packaging.py tests/test_input_project.py -v`

Expected: SDK staging and fallback APIs are absent; required resource staging still returns `False`.

- [ ] **Step 4: Implement strict staging and SDK path ownership**

Use one private tree-copy helper with explicit allowed roots and excluded names. In required mode, raise `PackagingError` naming each missing source. Stage only `sdk/vs-bench/pyproject.toml`, `README.md`, and `src/**`; never stage SDK tests or its local build artifacts.

`sdk_paths.sdk_root()` mirrors `resource_paths.resources_root()`: return `PROJECT_ROOT / "sdk"` when it exists, otherwise return `importlib.resources.files("vibesys") / "_sdk"` when present.

Normalize SDK declarations lexically. Accept only paths whose normalized components contain `sdk` followed by one or more non-parent components. Prefer the resolved checkout path when it is beneath the checkout SDK root and installable. Otherwise map the suffix after `sdk` beneath the packaged root and revalidate containment plus `pyproject.toml`.

- [ ] **Step 5: Wire staging into wheel and sdist inputs**

Call `stage_sdk` from `build_py` after Python package copying. Include build helpers, internal package sources, resources, SDK sources, and frontend sources in `MANIFEST.in`, fixing the existing helper-module omission in sdists. Add `_sdk/**/*` to root package data.

- [ ] **Step 6: Run the resource/SDK contract tests**

Run:

```bash
uv run pytest tests/test_resources_packaging.py tests/test_sdk_packaging.py tests/test_input_project.py tests/test_context.py -v
uv build --sdist
uv build --wheel dist/vibesys-0.1.0.tar.gz
```

Expected: tests pass and the source archive can build a valid headless development wheel without importing missing helper modules.

- [ ] **Step 7: Commit resource and SDK packaging**

```bash
git add resources_packaging.py src/vibesys/sdk_paths.py src/vibesys/input_project.py setup.py pyproject.toml MANIFEST.in tests
git commit -m "Package agent resources and input SDK"
```

---

### Task 3: Build and run a self-contained TUI payload

**Files:**
- Rewrite: `tui_packaging.py`
- Create: `wheel_targets.py`
- Create: `scripts/build_release_wheel.py`
- Create: `third_party/bun/LICENSE`
- Create: `clients/tui/src/self-test.ts`
- Modify: `clients/tui/package.json`
- Modify: `clients/tui/tsconfig.json`
- Modify: `clients/tui/vitest.config.ts`
- Modify: `src/vibesys/cli.py`
- Modify: `setup.py`
- Rewrite: `tests/test_tui_packaging.py`
- Modify: `tests/test_cli_launcher.py`
- Create: `tests/test_wheel_targets.py`

**Interfaces:**
- Produces: `WheelTarget(key, system, machine, wheel_platform, opentui_package, bun_asset)`
- Produces: `resolve_wheel_target(key: str, *, host_system: str, host_machine: str) -> WheelTarget`
- Produces: `stage_prebuilt_tui(source: Path | None, dest: Path, *, required: bool) -> bool`
- Produces: `build_release_wheel(target_key: str, bun: Path, output_dir: Path) -> Path`
- Produces: `BundledTui(root: Path, runtime: Path, launcher: Path)` from `vibesys.cli.bundled_tui()`
- Consumes: Tasks 1 and 2's setuptools build and staged package-data paths

- [ ] **Step 1: Write target and staging failures first**

```python
@pytest.mark.parametrize("key", ["linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-arm64"])
def test_supported_target_round_trip(key):
    target = TARGETS[key]
    assert resolve_wheel_target(key, host_system=target.system, host_machine=target.machine) == target

def test_required_tui_rejects_missing_payload(tmp_path):
    with pytest.raises(TuiPackagingError, match="launcher.js"):
        stage_prebuilt_tui(tmp_path / "missing", tmp_path / "dest", required=True)
```

Also test mismatched host/target, unexpected OpenTUI native packages, wrong Bun version manifest, missing license, missing executable bit, source maps, and idempotent staging.

- [ ] **Step 2: Write launcher tests for the bundled runtime contract**

Build a fake payload containing `bin/bun` and `app/dist/launcher.js`. Assert that interactive launch executes the bundled absolute Bun path, sets `VIBESYS_TUI_RUNTIME` to that path, sets `BUN_CONFIG_SKIP_INSTALL_PACKAGES=1`, preserves `VIBESYS_PYTHON`, and never calls `shutil.which`. Assert a missing or non-executable bundled runtime fails directly rather than searching the host.

- [ ] **Step 3: Run target, staging, and launcher tests to verify failure**

Run: `uv run pytest tests/test_wheel_targets.py tests/test_tui_packaging.py tests/test_cli_launcher.py -v`

Expected: new interfaces are absent and existing launcher tests demonstrate system-runtime lookup.

- [ ] **Step 4: Implement target definitions and passive setuptools staging**

Define all four exact target records in `wheel_targets.py`. Replace `tui_packaging` package-manager detection and wheel-time installation with validation and copying of the directory named by `VIBESYS_TUI_BUNDLE`. `build_py` treats it as optional for development and required whenever `VIBESYS_WHEEL_TARGET` is set.

- [ ] **Step 5: Implement the release payload builder**

The builder must:

```text
1. Resolve and host-validate the target.
2. Verify `bun --version` is exactly 1.3.9.
3. Run `pnpm install --frozen-lockfile` and `pnpm --dir clients/tui build`.
4. Run `pnpm --filter @vibesys/tui deploy --prod <temporary app dir>`.
5. Retain only the target's `@opentui/core-<platform>` optional package.
6. Remove source maps and development-only files from temporary staging.
7. Copy Bun with mode 0755 and copy committed Bun/OpenTUI licenses.
8. Write `manifest.json` containing target, Bun version, TUI version, and SHA-256 hashes.
9. Invoke `uv build --wheel` with `VIBESYS_WHEEL_TARGET` and `VIBESYS_TUI_BUNDLE`.
10. Return the one wheel created, rejecting zero or multiple outputs.
```

All subprocess calls use argument arrays, explicit working directories, checked exit status, and injectable runners in unit tests. Work occurs in temporary directories and never prunes the repository's `node_modules`.

- [ ] **Step 6: Make the installed CLI runtime-self-contained**

Replace runtime discovery with `bundled_tui()`. Invoke:

```python
env = {
    **os.environ,
    "VIBESYS_PYTHON": sys.executable,
    "VIBESYS_TUI_RUNTIME": str(bundle.runtime),
    "BUN_CONFIG_SKIP_INSTALL_PACKAGES": "1",
}
return subprocess.call([str(bundle.runtime), str(bundle.launcher), *args], env=env)
```

Keep the existing headless routing. Development installs without a payload may fall back to headless; release verification rejects such wheels.

- [ ] **Step 7: Add a native OpenTUI self-test and stabilize frontend tests**

`self-test.ts` imports OpenTUI's test renderer, creates a 20x4 renderer, writes one text node, verifies the rendered frame contains `vibesys`, destroys the renderer, and exits zero. Configure Vitest to use a Bun-compatible non-worker pool or an explicit single-process pool, proven under Bun 1.3.9, instead of relying on the current worker `MessagePort.addListener` behavior.

- [ ] **Step 8: Run focused Python and frontend verification**

Run:

```bash
uv run pytest tests/test_wheel_targets.py tests/test_tui_packaging.py tests/test_cli_launcher.py -v
pnpm --dir clients/tui check
pnpm --dir clients/tui test
pnpm --dir clients/tui build
```

Expected: all tests pass under Bun 1.3.9 and `dist/self-test.js` exists.

- [ ] **Step 9: Commit the self-contained payload implementation**

```bash
git add tui_packaging.py wheel_targets.py scripts/build_release_wheel.py third_party/bun clients/tui src/vibesys/cli.py setup.py tests
git commit -m "Build self-contained platform TUI payloads"
```

---

### Task 4: Emit and verify platform wheels in clean environments

**Files:**
- Modify: `setup.py`
- Create: `scripts/verify_release_wheel.py`
- Create: `scripts/verify_installed_release.py`
- Create: `packaging/release-wheel.Dockerfile`
- Create: `scripts/test_release_wheel.sh`
- Create: `tests/test_release_wheel_verifier.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: platform-aware `bdist_wheel` command using `VIBESYS_WHEEL_TARGET`
- Produces: `verify_wheel(wheel: Path, source_root: Path, target: WheelTarget) -> None`
- Produces: `scripts/test_release_wheel.sh <wheel>` clean-room entry point
- Consumes: Task 3's target record, payload manifest, and wheel path

- [ ] **Step 1: Write verifier tests with deliberately malformed wheels**

Create small ZIP fixtures and assert rejection of a universal filename, missing internal package, `Requires-Dist: vs-sandbox`, missing `resources/skills`, missing `sdk/vs-bench`, wrong target payload, multiple OpenTUI native packages, absent Bun license, oversized file, and mismatched `METADATA` version. Add one complete fixture that passes.

- [ ] **Step 2: Run verifier tests and confirm failure**

Run: `uv run pytest tests/test_release_wheel_verifier.py -v`

Expected: `verify_release_wheel` is missing.

- [ ] **Step 3: Implement platform-aware wheel tags**

Subclass `wheel.bdist_wheel.bdist_wheel`. With no target environment, retain the ordinary pure development wheel. With a target, set `root_is_pure = False` and return `(py3, none, target.wheel_platform)` from `get_tag`. Validate the host again before building. Add `wheel>=0.45` to PEP 517 build requirements.

- [ ] **Step 4: Implement static wheel verification**

Read the wheel as ZIP plus `email.parser` metadata. Compare every tracked source file beneath the resource inclusion roots with its packaged path and SHA-256 digest. Verify all six top-level framework packages, `py.typed` files, SDK project files, scripts, licenses, payload manifest, executable mode, platform filename/tag, version, dependency metadata, one target native package, and the PyPI 100 MB file limit. Reject repository paths or source-map files in archived names.

- [ ] **Step 5: Implement installed-artifact verification**

Run under the installed tool interpreter and assert:

```python
for package in FRAMEWORK_PACKAGES:
    module = importlib.import_module(package)
    assert "site-packages" in str(Path(module.__file__).resolve())
    assert importlib.metadata.packages_distributions()[package] == ["vibesys"]
```

Then resolve skills and profilers, materialize a minimal input project whose missing checkout-relative source is `../../../sdk/vs-bench`, run `uv sync` for that copied project, import `vs_bench` there, invoke the bundled Bun on `dist/self-test.js`, and invoke the bundled launcher on backend `--help`. Assert no `bun`, `node`, `npm`, or `pnpm` exists on the sanitized runtime `PATH`.

- [ ] **Step 6: Add the two-stage Linux clean-room test**

The Docker runtime stage starts from `python:3.12-slim`, copies only uv, the wheel, and the verifier, uses empty `HOME`, `UV_CACHE_DIR`, `UV_TOOL_DIR`, and `UV_TOOL_BIN_DIR`, sets `PYTHONNOUSERSITE=1`, clears `PYTHONPATH`, installs the wheel with `uv tool install --no-cache --no-config`, removes the wheel, changes to `/tmp`, and runs installed verification. It must not mount the repository at runtime.

- [ ] **Step 7: Run a real Linux x86_64 build and clean-room test**

Run:

```bash
uv run python scripts/build_release_wheel.py --target linux-x86_64 --bun /path/to/bun-1.3.9 --output-dir dist/release
uv run python scripts/verify_release_wheel.py dist/release/*.whl --target linux-x86_64
./scripts/test_release_wheel.sh dist/release/*.whl
```

Expected: the exact wheel installs with no checkout or system JavaScript runtime, all installed checks pass, and interactive native initialization exits zero.

- [ ] **Step 8: Commit artifact verification**

```bash
git add setup.py pyproject.toml packaging scripts tests/test_release_wheel_verifier.py
git commit -m "Verify release wheels in clean environments"
```

---

### Task 5: Add native CI and Trusted Publishing

**Files:**
- Create: `.github/workflows/publish.yml`
- Modify: `.github/workflows/test.yml`
- Create: `docs/publishing.md`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `clients/tui/package.json`
- Create: `scripts/check_release_version.py`
- Create: `tests/test_release_version.py`

**Interfaces:**
- Produces: PR/release matrix job for `linux-x86_64`, `linux-aarch64`, `macos-x86_64`, and `macos-arm64`
- Produces: Trusted Publishing job guarded by the `pypi` environment
- Produces: `check_release_version(tag: str | None) -> Version`
- Consumes: Task 4's build and verification commands

- [ ] **Step 1: Add failing version-consistency tests**

Test agreement among root `project.version`, `clients/tui/package.json`, an optional `refs/tags/vX.Y.Z`, wheel filenames, and wheel `METADATA`. Reject non-PEP-440 versions, non-`v` tags, and mismatches.

- [ ] **Step 2: Implement metadata and version validation**

Add MIT SPDX license metadata, repository and issue URLs, Python and OS classifiers, and supported-platform documentation. Keep version `0.1.0` synchronized in Python and TUI metadata. Add the exact user command `uv tool install vibesys` and explain that external coding-agent CLIs and credentials remain user prerequisites.

- [ ] **Step 3: Add the four-target build workflow**

Use this matrix shape:

```yaml
matrix:
  include:
    - target: linux-x86_64
      runner: ubuntu-24.04
    - target: linux-aarch64
      runner: ubuntu-24.04-arm
    - target: macos-x86_64
      runner: macos-13
    - target: macos-arm64
      runner: macos-14
```

Each job checks out with submodules disabled, installs pinned uv, Python 3.12,
pnpm 11.11.0, and Bun 1.3.9, builds one wheel, runs static and installed
verification natively, and uploads an artifact named by target. The PR trigger
never grants publishing permissions.

- [ ] **Step 4: Add artifact aggregation and Trusted Publishing**

On `release: published`, download all four artifacts into one directory, run the aggregate verifier to require exactly the expected wheel set and tag version, then publish with `pypa/gh-action-pypi-publish` in the protected `pypi` environment. The job has `permissions: id-token: write` and no API-token secret. Pin third-party actions to reviewed commit SHAs.

- [ ] **Step 5: Document external setup and recovery**

`docs/publishing.md` must specify the PyPI pending Trusted Publisher tuple (owner `uw-syfi`, repository `vibesys`, workflow `publish.yml`, environment `pypi`), GitHub environment creation, release-candidate tag flow, verification from a fresh machine, immutability of published versions, and how to yank rather than overwrite a bad release.

- [ ] **Step 6: Run local workflow-facing checks**

Run:

```bash
uv run pytest tests/test_release_version.py -v
uv run python scripts/check_release_version.py
pnpm check:ts
uv run twine check dist/release/*.whl
uv run check-wheel-contents dist/release/*.whl
```

Expected: all checks pass and the workflow YAML parses with actionlint when available.

- [ ] **Step 7: Commit release automation and docs**

```bash
git add .github/workflows pyproject.toml clients/tui/package.json README.md docs/publishing.md scripts/check_release_version.py tests/test_release_version.py
git commit -m "Add verified PyPI publishing workflow"
```

---

### Task 6: Full verification, review, and PR delivery

**Files:**
- Modify only files required by failures found during verification
- Use: `.github/pull_request_template.md`

**Interfaces:**
- Consumes: every artifact and verifier from Tasks 1 through 5
- Produces: a ready GitHub pull request with green required checks

- [ ] **Step 1: Run formatting, lint, and types**

```bash
./scripts/format.sh
./scripts/check_format.sh
./scripts/check_lint.sh
./scripts/check_types.sh
pnpm check:ts
pnpm --dir clients/tui check
```

- [ ] **Step 2: Run all automated tests**

```bash
uv run pytest
pnpm --dir clients/tui test
pnpm --dir clients/tui build
```

Expected: all tests pass, with only documented OS-specific skips.

- [ ] **Step 3: Rebuild from a clean Git archive**

Create a temporary archive from `HEAD`, build Linux x86_64 without mounting the original worktree into the build or runtime phases, run static verification, then run the Docker clean-room test. Confirm no path inside the result references the source worktree.

- [ ] **Step 4: Audit the completion contract**

Check each design requirement against artifact evidence: six framework imports, full resource hash manifest, packaged SDK materialization, no `vs-*` metadata, bundled Bun only, offline native self-test, exact platform tag, wheel size, valid metadata, no root sdist in upload set, four workflow targets, and publish permissions limited to Trusted Publishing.

- [ ] **Step 5: Review the full diff**

Run:

```bash
git status --short --branch
git diff origin/main...HEAD --stat
git diff origin/main...HEAD
```

Remove generated build output and unrelated changes. Confirm every changed file serves the publishing contract.

- [ ] **Step 6: Commit any verification fixes and push**

```bash
git add -u
git add packaging_support.py wheel_targets.py MANIFEST.in third_party/bun/LICENSE \
  scripts/build_release_wheel.py scripts/verify_release_wheel.py \
  scripts/verify_installed_release.py scripts/test_release_wheel.sh \
  scripts/check_release_version.py packaging/release-wheel.Dockerfile \
  src/vibesys/sdk_paths.py clients/tui/src/self-test.ts \
  docs/publishing.md tests/test_distribution_packaging.py \
  tests/test_sdk_packaging.py tests/test_wheel_targets.py \
  tests/test_release_wheel_verifier.py tests/test_release_version.py
git diff --staged --check
git commit -m "Harden release verification"
git push -u origin vic/pypi-publish-ready
```

- [ ] **Step 7: Open a ready PR from the repository template**

Use a concrete title such as `Publish self-contained VibeSys wheels to PyPI`. Fill `Problem`, `Solution`, `Architecture`, `Verification`, `Correctness properties`, and `Testing`, including exact command results and the four-wheel matrix. Open ready for review, not draft, because the user requested a passing PR.

- [ ] **Step 8: Monitor CI and fix every relevant failure**

Inspect all GitHub checks and logs. For each failure, use systematic debugging, reproduce locally where possible, add or strengthen a regression test, commit the focused fix, push, and wait again. Completion requires all required PR checks green and the PR mergeable.
