# scripts/

Release and AUR automation for droidproxy-linux. Written as an internal
runbook; safe to feed to an AI agent as context before asking it to cut a
release, publish an AUR package, or debug a failed publish.

## Layout

| File | Kind |
|---|---|
| [`_lib.sh`](./_lib.sh) | Shared bash helpers. Sourced by the others. Not executable on its own. |
| [`bump-version.sh`](./bump-version.sh) | Edits every version string, runs tests, commits, tags. Does **not** push. |
| [`test-pkgbuild.sh`](./test-pkgbuild.sh) | Local `makepkg` dry-run. `--source` uses `git archive`; `--bin` uses the GitHub release. |
| [`publish-aur.sh`](./publish-aur.sh) | Full AUR push for `droidproxy-linux` and/or `droidproxy-linux-bin`. |

All scripts:

- Start with `set -euo pipefail`.
- Use `builtin cd` so zoxide / `z` aliases can never redirect the working directory.
- Resolve the repo root from `${BASH_SOURCE[0]}`, so they work from any cwd.
- Source [`_lib.sh`](./_lib.sh) at the top.
- Register scratch dirs via `register_cleanup` + single `EXIT` trap; no leftovers on success or failure.

## Preconditions per script

### `bump-version.sh <X.Y.Z>`

| Check | Enforced by |
|---|---|
| New version matches `X.Y.Z` regex | `require_semver` in `_lib.sh` |
| Working tree is clean (no unstaged or staged changes) | `require_clean_tree` in `_lib.sh` |
| All four version strings currently in sync with the pyproject version | reads `src/pyproject.toml`, compares to `current_version` |
| pytest suite passes (95 tests) | only when `src/.venv/bin/python` exists; otherwise warn |
| `ruff check src/src src/tests` passes | same |
| User confirms the diff interactively | `confirm` prompt |

Files touched, in order:

1. `src/pyproject.toml` — `version = "X.Y.Z"`
2. `src/src/droidproxy/__init__.py` — `__version__ = "X.Y.Z"`
3. `src/packaging/PKGBUILD` — `pkgver=X.Y.Z`, `pkgrel=1`
4. `src/packaging/PKGBUILD-bin` — same

Each file is `grep`-verified to contain the new version after the sed pass; if any
file is missed, the script aborts with a clear error before committing.

Git operations:

- `git add` only those four files
- `git commit -m "chore: bump to X.Y.Z"`
- `git tag -a vX.Y.Z -m "DroidProxy Linux X.Y.Z"`

**Deliberate non-goal:** the script never pushes. It prints the exact
`git push origin <branch> vX.Y.Z` line and exits, so a mistake (wrong
version, failing tests, unwanted diff) is a `git reset --hard HEAD~1 &&
git tag -d vX.Y.Z` away, with no cleanup needed on `origin`.

### `test-pkgbuild.sh [--source|--bin]`

| Check | Enforced by |
|---|---|
| `makepkg`, `updpkgsums`, `sudo` on PATH | `require_cmd` |
| `--bin` only: tag `v$pkgver` exists on `origin` | `check_github_tag` |

Both variants execute the same shape:

1. Copy `PKGBUILD` (or `PKGBUILD-bin`) into a fresh `mktemp -d` scratch dir.
2. For `--source`, rewrite `source=...` to `file:///tmp/droidproxy-linux-$PKGVER.tar.gz`
   pointing at a `git archive HEAD` snapshot. Bypasses the need for a GitHub tag.
3. `updpkgsums` → real `sha256sums` values.
4. `makepkg -sri --noconfirm` → build + install.
5. `droidproxy --version` → smoke check (does not validate the version number here;
   the next script does).
6. `sudo pacman -R --noconfirm <pkgname>` → clean uninstall.
7. `makepkg --printsrcinfo > .SRCINFO`.
8. `namcap PKGBUILD` + built package (warnings only, non-fatal).

Cleanup: scratch dir is removed on EXIT via trap. The `/tmp/droidproxy-linux-$PKGVER.tar.gz`
tarball is intentionally kept in `--source` mode so you can reinspect it.

### `publish-aur.sh [--source|--bin|--both]` (default `--both`)

| Check | Enforced by |
|---|---|
| AUR SSH auth works (`ssh aur@aur.archlinux.org help`) | `check_aur_ssh` |
| Tag `v$pkgver` exists on `origin` | `check_github_tag` |
| `droidproxy --version` after local install equals `$pkgver` | explicit check after `makepkg -sri` |

Per selected variant the script:

1. Runs the full `test-pkgbuild.sh`-style build + install + uninstall smoke.
2. Regenerates `.SRCINFO`.
3. `git clone ssh://aur@aur.archlinux.org/<pkgname>.git` into a second scratch dir.
4. Copies the verified `PKGBUILD` and `.SRCINFO` in.
5. If nothing changed (re-run on the same version), skips the commit and prints a notice.
6. Otherwise commits:
   - "Initial upload: `<pkgname>` `<pkgver>-1`" when the AUR repo has no prior commits
   - "Bump `<pkgname>` to `<pkgver>-1`" otherwise
7. `git push origin master`.

AUR uses `master`, not `main`, as the default branch for every package repo.
Do not "modernise" this — AUR tooling treats `master` as canonical.

## Typical workflows

### 1. Release a new version

```bash
scripts/bump-version.sh 1.8.10
git push origin $(git rev-parse --abbrev-ref HEAD) v1.8.10
# linux-release.yml builds AppImages + wheel, attaches to the GitHub release
scripts/publish-aur.sh --both
```

If you want to sanity-check the PKGBUILD against your HEAD **before** tagging:

```bash
scripts/test-pkgbuild.sh               # --source, no release required
```

### 2. Initial publish of `-bin` after a prior `--source` push

```bash
scripts/publish-aur.sh --bin
```

Must run after CI has attached `DroidProxyLinux-{x86_64,aarch64}.AppImage` to
the `v$pkgver` release. Before that, `updpkgsums` on `PKGBUILD-bin` will 404.

### 3. PKGBUILD-only fix (no Python changes)

If you change `src/packaging/PKGBUILD` without bumping the version (e.g. to add
a missing dep), increment `pkgrel` manually:

```bash
sed -i 's/^pkgrel=1$/pkgrel=2/' src/packaging/PKGBUILD
git commit -am "packaging: bump pkgrel to 2 (add missing dep)"
scripts/publish-aur.sh --source
```

No tag required, and no wait for CI. Just push the updated PKGBUILD to AUR.

### 4. Hotfix that requires a new upstream tarball

Use the full release flow (option 1). Don't try to attach fixed assets to an
existing tag — AUR users will have stale sha256sums.

## Failure modes and fixes

### `bump-version.sh` aborts with "working tree has uncommitted changes"

Stash or commit first:

```bash
git stash -u && scripts/bump-version.sh X.Y.Z && git stash pop
```

### `bump-version.sh` aborts because `grep` didn't find the new version in a file

Means a previous release was not sync'd (e.g. someone edited `__version__` by
hand to a non-semver value). Fix by hand, then re-run.

### `test-pkgbuild.sh` / `publish-aur.sh` fails with `ModuleNotFoundError` during `droidproxy --version`

A Python runtime dep is missing from the PKGBUILD's `depends=()` list.
`pyproject.toml` tells pip what to install; pacman doesn't read it. Every runtime
import needs a matching Arch package.

Current mapping:

| PyPI | Arch package | Declared in `src/packaging/PKGBUILD`? |
|---|---|---|
| `aiohttp` | `python-aiohttp` | yes |
| `watchdog` | `python-watchdog` | yes |
| `platformdirs` | `python-platformdirs` | yes |
| `tomli-w` | `python-tomli-w` | yes |
| `psutil` | `python-psutil` | yes |
| `PyGObject` | `python-gobject` | yes |
| (GTK runtime) | `gtk3`, `libayatana-appindicator` | yes |
| (browser) | `xdg-utils` | yes |

To verify nothing is missing:

```bash
grep -rhE '^import |^from \w' src/src/droidproxy/*.py | sort -u
```

cross-referenced against `depends=()` in `src/packaging/PKGBUILD`.

### `publish-aur.sh` aborts with "cannot authenticate to aur@aur.archlinux.org"

1. `cat ~/.ssh/github.pub` (or whichever pubkey you registered)
2. Upload at <https://aur.archlinux.org/account/> in the "SSH Public Key" field
3. Verify `~/.ssh/config` has:

   ```
   Host aur.archlinux.org
       User aur
       IdentityFile ~/.ssh/github
       IdentitiesOnly yes
   ```

4. `ssh -v aur@aur.archlinux.org help` must print `Welcome, <yourname>`

### `publish-aur.sh` aborts with "tag v$pkgver is not pushed to origin"

```bash
git push origin v$pkgver
```

If the tag doesn't exist locally either, you skipped `bump-version.sh`. Go back
and run it.

### `updpkgsums` inside a script fails with 404

Three common causes, in order of likelihood:

1. **Tag not pushed yet** (most common) → `git push origin v$pkgver`.
2. **--bin path, CI hasn't finished** → check the Actions tab of the GitHub
   repo; wait for `linux-release.yml` to finish and attach the AppImages.
3. **PKGBUILD `url=` points at the wrong repo** → current correct value:
   `https://github.com/StressarN/droidproxy-linux`.

### `makepkg` extracts the tarball to the wrong directory name

Symptom: `cd "$srcdir/droidproxy-1.8.8/src": No such file or directory`.

Root cause: GitHub names the tarball's top-level directory after the **repo**,
not after `pkgname`. The repo is `droidproxy-linux`, so the extracted dir is
`droidproxy-linux-1.8.8/`. The PKGBUILD's `build()` / `package()` use
`${pkgname}-${pkgver}`, which works only because `pkgname=droidproxy-linux`.

Never change `pkgname` to something that doesn't match the GitHub repo name
without also switching `cd "$srcdir/${pkgname}-${pkgver}"` to a hard-coded
`cd "$srcdir/droidproxy-linux-${pkgver}"`.

### AUR push succeeded but `paru -S droidproxy-linux` says "unknown package"

AUR indexes propagate within seconds, but browser caches can lag. Check
<https://aur.archlinux.org/packages/droidproxy-linux> directly; if the page
exists, tell users to `paru -Sy` first.

### `droidproxy --version` inside `publish-aur.sh` prints the wrong number

The script fails loudly. Root cause is almost always that a previous install of
a different version wasn't cleaned up and `which droidproxy` resolves to
`/home/…/src/.venv/bin/droidproxy` (old editable install) instead of
`/usr/bin/droidproxy` (just-installed package).

```bash
which droidproxy        # should be /usr/bin/droidproxy immediately after install
sudo pacman -R droidproxy-linux
pipx uninstall droidproxy || true
rm -rf ~/.local/bin/droidproxy
```

…then re-run the script.

## GitHub Actions integration

| Workflow | Triggered by | What it produces |
|---|---|---|
| `linux-release.yml` | `v*` tag push or manual dispatch | Wheel + sdist in PyPI-ready format, plus `DroidProxyLinux-{x86_64,aarch64}.AppImage` (+ `.zsync`), attached to the GitHub release |
| `update-cliproxyapi-linux.yml` | 12-hour cron + manual dispatch | PR that bumps `PINNED_VERSION` in `src/src/droidproxy/binary.py` when upstream `router-for-me/CLIProxyAPI` cuts a new release |

The scripts here do **not** invoke CI; they just rely on its outputs
(`--bin` needs AppImages on the release page). If CI fails or hasn't
finished, `publish-aur.sh --bin` aborts at `updpkgsums` with a 404 from GitHub.

## Files touched per operation

For audit / rollback purposes:

| Script | In-tree files | External side effects |
|---|---|---|
| `bump-version.sh` | `src/pyproject.toml`, `src/src/droidproxy/__init__.py`, `src/packaging/PKGBUILD`, `src/packaging/PKGBUILD-bin` | local git commit + tag only |
| `test-pkgbuild.sh` | none | scratch dir in `/tmp`, `sudo pacman -U` then `-R` of the built package |
| `publish-aur.sh` | none | scratch dirs in `/tmp`, `sudo pacman -U` then `-R`, `git push origin master` to `ssh://aur@aur.archlinux.org/<pkgname>.git` |

Neither `test-pkgbuild.sh` nor `publish-aur.sh` touches the repo-root
working tree. You can run them in the middle of a `git rebase` or while a
dev branch is checked out; both are fully isolated to their `mktemp -d`
scratch dirs.

## Extending the scripts

Conventions to follow if you add a fourth script or a flag:

- Source `_lib.sh` at the top. Don't duplicate `require_cmd`, `fail`, etc.
- Never call `cd` without `builtin` — zoxide aliases break scripts silently.
- Always `register_cleanup "$dir"` when you `mktemp -d`; the EXIT trap in
  `_lib.sh` handles removal.
- Never write outside `mktemp -d` or the explicit file list above.
- Don't push to GitHub on behalf of the user; print the exact command and exit.
- AUR pushes are the only exception — they're idempotent, safe to redo, and
  users expect `publish-aur.sh` to actually publish.
- Fail fast: use `fail "message"` (from `_lib.sh`) on every error path; it
  exits with code 1 and a red banner, which is easy to grep in CI logs.

## Related docs

- [`AGENTS.md`](../AGENTS.md) — project-wide overview, architecture, proxy
  behaviour contract. Read this first if you're new to the codebase.
- [`README.md`](../README.md) — end-user install + usage.
- [`SETUP.md`](../SETUP.md) — Factory Droid integration (customModels JSON).
