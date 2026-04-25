# AGENTS.md

## Build & Run

Package in `src/`, standard Python src-layout
(`src/pyproject.toml`, source `src/src/droidproxy/`, tests
`src/tests/`). Run from repo root.

```bash
# One-time venv (use --system-site-packages so the distro PyGObject is available)
python -m venv --system-site-packages src/.venv
src/.venv/bin/pip install -e 'src[dev,tray]'

# Run it
src/.venv/bin/droidproxy             # tray + web UI + daemon (default)
src/.venv/bin/droidproxy daemon      # headless (SSH / no tray)
src/.venv/bin/droidproxy doctor      # diagnose ports, binary, GTK, cloudflared
src/.venv/bin/droidproxy install-binary   # (re)download cli-proxy-api-plus
src/.venv/bin/droidproxy install-models   # apply DroidProxy models to ~/.factory/settings.json
src/.venv/bin/droidproxy install-droids   # copy Challenger Droid .md files into ~/.factory/

# Tests + lint (run after every change)
src/.venv/bin/python -m pytest src/tests   # ~95 tests, ~3 s
src/.venv/bin/ruff check src/src src/tests
```

Releases via GitHub Actions
(`.github/workflows/linux-release.yml`) on `v*` tags: matrix
AppImages (x86_64 + aarch64 + `.zsync`) + PyPI wheel/sdist.
No local packaging step like old
`create-app-bundle.sh`.

## Source Of Truth

Canonical:

- `src/pyproject.toml` -- deps, entry point, package data.
- `src/src/droidproxy/**/*.py` -- runtime code.
- `src/src/droidproxy/resources/config.yaml` -- bundled
  `cli-proxy-api-plus` config (`port: 8318`, localhost binding, Amp
  upstream, auth dir).
- `src/src/droidproxy/resources/web/{index.html,styles.css,app.js}` --
  settings UI at `http://127.0.0.1:8316/`.
- `src/src/droidproxy/resources/factory/{droids,commands}/*.md` --
  bundled Challenger Droid configs copied to `~/.factory/` by
  `install_challenger_droids`.
- `src/packaging/` -- `droidproxy.desktop`, `droidproxy.service`
  (systemd --user), `AppImageBuilder.yml`, `PKGBUILD` (source) and
  `PKGBUILD-bin` (AppImage) for AUR.

No other source tree. Old Swift `src/Sources/` and
macOS assets (`create-app-bundle.sh`, `appcast*.xml`, Sparkle
entitlements, `website/`, `graphify-out/`) removed when this port
became canonical. Don't re-add.

## Architecture

DroidProxy Linux = single Python process owning:

1. `ThinkingProxy` -- aiohttp HTTP server on `127.0.0.1:8317`
   (`src/src/droidproxy/proxy.py`).
2. `cli-proxy-api-plus` (upstream Go binary) as subprocess on
   `127.0.0.1:8318`, managed by `ServerManager`
   (`src/src/droidproxy/backend.py`). Binary downloaded + SHA-256
   verified on first run by `src/src/droidproxy/binary.py`, version
   pinned in `PINNED_VERSION`.
3. `WebUI` -- aiohttp app on `127.0.0.1:8316`
   (`src/src/droidproxy/web.py`) serving settings UI + `/api/*`.
4. `AuthWatcher` -- `watchdog` observer on `~/.cli-proxy-api/` fanning
   OAuth file-changes over SSE
   (`src/src/droidproxy/auth.py`).
5. Optional `TrayApp` -- GTK `AyatanaAppIndicator3` tray icon
   (`src/src/droidproxy/tray.py`). GTK on main thread;
   asyncio loop on background daemon thread; menu callbacks
   marshal via `loop.call_soon_threadsafe(asyncio.ensure_future, coro)`.
6. `TunnelManager` (`src/src/droidproxy/tunnel.py`) -- optional
   `cloudflared tunnel --url` wrapper; never auto-started.
7. `Updater` (`src/src/droidproxy/updater.py`) -- daily GitHub release
   poll, install-method-aware upgrade (AppImageUpdate
   / pipx / AUR hint).

Glue:

- `src/src/droidproxy/context.py` -- `AppContext` dataclass owns
  long-lived singletons.
- `src/src/droidproxy/app.py` -- `run_daemon()` and `run_with_tray()`
  handle asyncio/GTK, signals, startup
  order (proxy → backend → auth watcher → web UI → updater).
- `src/src/droidproxy/cli.py` -- argparse entry
  (`droidproxy = droidproxy.cli:main`).

Request flow:

`Client -> :8317 ThinkingProxy -> :8318 cli-proxy-api-plus -> upstream provider`

## Current ThinkingProxy behavior

Byte-stable port of Swift `ThinkingProxy`. Injector
(`src/src/droidproxy/injector.py`) edits raw JSON strings via regex so
Anthropic's prompt cache hits. Never replace regex
with `json.dumps`, never reorder keys. Snapshot tests
in `src/tests/test_injector.py` are the contract.

On `POST` JSON bodies:

- Claude adaptive thinking for models containing `opus-4-7` or
  `sonnet-4-6`. Injects `"thinking":{"type":"adaptive"}` and
  `"output_config":{"effort":"..."}`, forces `"stream":true`. Effort from
  `Preferences.opus47_thinking_effort` /
  `Preferences.sonnet46_thinking_effort`.
- Codex reasoning for exact models `gpt-5.3-codex` and `gpt-5.5`.
  Injects `"reasoning":{"effort":"..."}`. Effort from
  `Preferences.gpt53_codex_reasoning_effort` /
  `Preferences.gpt55_reasoning_effort`.
- Gemini thinking for `gemini-3.1-pro-preview` and
  `gemini-3-flash-preview`. Injects
  `"generationConfig":{"thinkingConfig":{"thinking_level":"..."}}`.
  Level from `Preferences.gemini31_pro_thinking_level` /
  `Preferences.gemini3_flash_thinking_level`.
- Fast mode: injects `"service_tier":"priority"` when
  `/v1/responses` or `/api/v1/responses` hit with `gpt-5.5` /
  `gpt-5.3-codex`, matching `*_fast_mode` pref on, and
  client didn't set `service_tier`.
- Max Budget Mode: if `Preferences.claude_max_budget_mode` on,
  Sonnet 4.6 gets classic extended thinking
  (`max_tokens=64000`, `thinking={"type":"enabled","budget_tokens":63999}`,
  `output_config={"effort":"max"}`). Opus 4.7 intentionally
  unaffected, keeps adaptive path.
- Gemini on Responses API: rewrites `/v1/responses` →
  `/v1/chat/completions` (and `/api/` twin) when JSON body's
  `model` starts `gemini-`, since CLIProxyAPIPlus doesn't expose
  Gemini via Responses API.

Does NOT:

- Strip/normalize model suffixes.
- Send `thinking.budget_tokens` to Opus 4.7 (400 reject).
- Add `anthropic-beta` interleaved-thinking headers (adaptive
  enables interleaving auto).
- Implement `-thinking-N` suffix branching from
  legacy Swift docs.
- Retry on upstream 404 with `/api/` prefix (old Swift
  helper was dead code).

### Amp routing

Port of Swift Amp passthrough:

- `/auth/cli-login` and `/api/auth/cli-login` → 302 to
  `https://ampcode.com/...` (strips `/api/` prefix).
- `/provider/*` rewritten to `/api/provider/*`.
- Any non-`/api/provider/*` and non-`/v1/*` / non-`/api/v1/*` path
  treated as Amp management, forwarded to
  `https://ampcode.com` with Location + cookie-domain rewrites so
  browser stays on localhost.

## Auth And Providers

Provider keys in UI: `claude`, `codex`, `gemini`.

Auth data lives in `~/.cli-proxy-api/` as JSON, managed by Go
binary. `AuthManager` reads:

- `type`
- `email`
- `login`
- `expired` (ISO-8601 with/without fractional seconds)
- `disabled`

Preserve:

- Multiple accounts per provider.
- Per-account disable/enable via `disabled` field.
- Last enabled account per provider can't be disabled (guard
  in `AuthManager.toggle_disabled`).
- Provider-level toggles in web UI separate from per-account
  disable.
- Provider-level disable writes `oauth-excluded-models` to
  `~/.cli-proxy-api/merged-config.yaml` (generated by
  `ServerManager._write_merged_config`, 0600, atomic rename).
- `cli-proxy-api-plus` hot-reloads `merged-config.yaml`, so provider
  toggle never restarts subprocess.
- `AuthWatcher` (`watchdog.Observer`) rescans `~/.cli-proxy-api/` with
  ~0.5 s debounce, pushes snapshots over
  `GET /api/auth/stream` (SSE).

## Preferences

`src/src/droidproxy/prefs.py` -- TOML-backed replacement for
`AppPreferences.swift`. File at
`$XDG_CONFIG_HOME/droidproxy/config.toml`.

Keys + defaults (must match macOS Swift defaults --
changing invalidates user installs):

| Key | Default | Valid |
|---|---|---|
| `opus47_thinking_effort` | `xhigh` | `low/medium/high/xhigh/max` |
| `sonnet46_thinking_effort` | `high` | `low/medium/high/max` |
| `gpt53_codex_reasoning_effort` | `high` | `low/medium/high/xhigh` |
| `gpt55_reasoning_effort` | `high` | `low/medium/high/xhigh` |
| `gemini31_pro_thinking_level` | `high` | `low/medium/high` |
| `gemini3_flash_thinking_level` | `high` | `minimal/low/medium/high` |
| `gpt53_codex_fast_mode` | `false` | bool |
| `gpt55_fast_mode` | `false` | bool |
| `claude_max_budget_mode` | `false` | bool |
| `allow_remote` | `false` | bool |
| `secret_key` | `""` | string |
| `oled_theme` | `false` | bool |
| `enabled_providers` | `{claude: true, codex: true, gemini: true}` | dict[str, bool] |

Writes atomic via `tempfile` + `os.replace`. `PreferencesStore`
thread-safe singleton; always use `get_store()` so web UI,
proxy, tray see same state.

## Key Files

| File | Role |
|---|---|
| `src/src/droidproxy/cli.py` | argparse entry (`droidproxy` command), subcommands (`tray`, `daemon`, `install-droids`, `install-models`, `install-binary`, `doctor`, `check-update`, `paths`). |
| `src/src/droidproxy/app.py` | Daemon/tray orchestrator, startup order, SIGTERM, asyncio/GTK split. |
| `src/src/droidproxy/context.py` | `AppContext` dataclass owning singletons (`prefs`, `server`, `proxy`, `auth_manager`, `auth_watcher`, `tunnel`, `updater`). |
| `src/src/droidproxy/proxy.py` | aiohttp HTTP proxy on 8317 (inbound rewrites, Amp passthrough, streaming, upstream forwarder). |
| `src/src/droidproxy/injector.py` | Pure, unit-testable JSON field surgery used by proxy. Regex identical to Swift. |
| `src/src/droidproxy/backend.py` | `ServerManager` -- spawns `cli-proxy-api-plus`, captures logs in ring buffer, runs `-claude-login` / `-codex-login` / `-login` with same stdin-nudging timings as Swift, kills orphans via `psutil`. |
| `src/src/droidproxy/binary.py` | Downloads + SHA-256-verifies pinned upstream release from `router-for-me/CLIProxyAPIPlus`. `PINNED_VERSION` bumped by `.github/workflows/update-cliproxyapi-linux.yml`. |
| `src/src/droidproxy/auth.py` | `AuthManager` + `AuthWatcher` -- watchdog rescan of `~/.cli-proxy-api/*.json` with SSE broadcast. |
| `src/src/droidproxy/prefs.py` | `PreferencesStore` atomic TOML persist + `get_store()` singleton. |
| `src/src/droidproxy/web.py` | aiohttp settings UI + `/api/*` endpoints (`status`, `prefs`, `auth`, `server`, `logs`, `factory/models`, `tunnel`, `droids`). |
| `src/src/droidproxy/tray.py` | `AyatanaAppIndicator3` tray menu. `gi` imported lazily in `run()` so `daemon` mode works without GTK. |
| `src/src/droidproxy/tunnel.py` | Optional `cloudflared` wrapper. Parses `https://*.trycloudflare.com` from stderr/stdout. |
| `src/src/droidproxy/updater.py` | GitHub release poller + install-method router (AppImage / pipx / AUR / source). No Sparkle. |
| `src/src/droidproxy/installer.py` | `install_challenger_droids` (copies `.md` to `~/.factory/`) + `install_factory_custom_models` (merges DroidProxy entries to `~/.factory/settings.json`, preserves other keys, scrubs legacy + `custom:CC:*` IDs). |
| `src/src/droidproxy/paths.py` | XDG path helpers (`config_dir`, `data_dir`, `state_dir`, `auth_dir`, `cli_proxy_api_binary`, resource lookups). |
| `src/src/droidproxy/resources/config.yaml` | Bundled CLIProxyAPIPlus template. Merged with user prefs to `~/.cli-proxy-api/merged-config.yaml` at runtime. |
| `src/src/droidproxy/resources/web/` | Vanilla JS settings page served by `WebUI`. |
| `src/tests/` | 95 tests (pytest + pytest-asyncio). `test_injector.py` is prompt-caching parity contract -- any failure = production bug. |

## Factory Integration

Two helpers in `installer.py`, mirrored as web endpoints + CLI
subcommands:

| Action | CLI | Endpoint | Effect |
|---|---|---|---|
| Apply DroidProxy models | `droidproxy install-models` | `POST /api/factory/models/apply` | Merges `DROID_PROXY_MODELS` into `~/.factory/settings.json`, scrubs `custom:droidproxy:*` + `custom:droidproxy:opus-4-6` (legacy) + `custom:CC:*`, re-indexes after user-added models, skips disabled-provider models. Atomic write. |
| Install Challenger Droids | `droidproxy install-droids` | `POST /api/droids/install` | Copies bundled `.md` files to `~/.factory/droids/` and `~/.factory/commands/`. |

Both respect provider toggle in `Preferences.enabled_providers`.

## Conventions

- Use stdlib `logging` (`log = logging.getLogger(__name__)`),
  never `print()` in runtime. `ThinkingProxy` also mirrors
  key events to `/tmp/droidproxy-debug.log` for parity with macOS
  `ThinkingProxy.fileLog`.
- Keep `src/src/droidproxy/injector.py` regex + semantics
  byte-identical to Swift. Comment block at top of
  `injector.py` documents contract. Any refactor changing
  output bytes = production regression (prompt cache breaks).
- Don't re-serialise JSON bodies in proxy; always
  edit raw string.
- Tray code optional. Nothing else may import `gi` /
  `gtk` at module scope -- tests run headless CI. If GTK needed,
  put inside function + guard with `TrayUnavailableError`.
- Local backend traffic stays on `127.0.0.1:8318`. Flip `allow-remote`
  only via `remote-management` section of merged config, driven by
  `allow_remote` / `secret_key` prefs.
- Always run `ruff check src/src src/tests` and
  `python -m pytest src/tests` before commit.
- Preserve `src/` src-layout. Package code at
  `src/src/droidproxy/`; don't flatten.
- Every preference change via `PreferencesStore.update` or
  `set_provider_enabled` (handles validation + atomic persist).
  Never write `config.toml` directly.

## Release Notes For Agents

Release pipeline:

- CI: `.github/workflows/linux-release.yml` on `v*` tags. Builds
  x86_64 + aarch64 AppImages (with `.zsync`), publishes wheel /
  sdist, attaches all to GitHub release via
  `softprops/action-gh-release`.
- Upstream bump: `.github/workflows/update-cliproxyapi-linux.yml`
  runs every 12 h, checks for new `router-for-me/CLIProxyAPIPlus`
  release, opens PR bumping `PINNED_VERSION` in
  `src/src/droidproxy/binary.py`. Binary not vendored --
  downloads at first launch with SHA-256 check against upstream
  `checksums.txt`.
- Distribution: AppImage (primary), PyPI wheel (`pipx install droidproxy`),
  AUR (`droidproxy` source + `droidproxy-bin` AppImage). PKGBUILDs
  in `src/packaging/`. Before pushing to AUR, populate real
  `sha256sums` with `updpkgsums` against GitHub release, regenerate
  `.SRCINFO` via `makepkg --printsrcinfo > .SRCINFO`, push to
  `ssh://aur@aur.archlinux.org/<pkgname>.git`.
- No Sparkle, notarization, code-signing, `.icns`. Updater
  opens AppImageUpdate (if installed) for AppImage
  users or prints install-method hint.
- Version in four places: `src/src/droidproxy/__init__.py`
  (`__version__`), `src/pyproject.toml` (`version = "..."`),
  `src/packaging/PKGBUILD` (`pkgver=...`), and
  `src/packaging/PKGBUILD-bin` (`pkgver=...`). Use
  `scripts/bump-version.sh` instead of hand-editing.

## Helper Scripts

All release + AUR plumbing lives in `scripts/`. Each script sources
`scripts/_lib.sh` for shared helpers (`require_cmd`,
`require_clean_tree`, `check_aur_ssh`, `check_github_tag`, version
readers, an EXIT-trap cleanup registry).

| Script | Purpose |
|---|---|
| `scripts/bump-version.sh <X.Y.Z>` | Rewrites every `version=`/`__version__`/`pkgver=` string, resets `pkgrel=1`, runs pytest + ruff against `src/.venv`, then `git commit` + annotated `git tag vX.Y.Z`. Does not push -- prints the exact push command. |
| `scripts/test-pkgbuild.sh [--source\|--bin]` | Local `makepkg` dry-run. `--source` (default) packages HEAD via `git archive` + a `file://` `source=` override so no GitHub tag is required. `--bin` exercises the AppImage PKGBUILD against the real `v$pkgver` release. Both install the built package, run `droidproxy --version`, uninstall, and lint via `namcap`. |
| `scripts/publish-aur.sh [--source\|--bin\|--both]` | Full AUR push. Preflights AUR SSH auth + that `v$pkgver` exists on `origin`. For each selected variant: `updpkgsums` + `makepkg -sri` + smoke-test + `pacman -R` + `.SRCINFO` regen, then clones `ssh://aur@aur.archlinux.org/<pkgname>.git`, commits, pushes `origin master`. EXIT trap cleans up scratch dirs. |

Typical release flow:

```bash
scripts/bump-version.sh 1.8.10
git push origin main v1.8.10          # triggers linux-release.yml
scripts/test-pkgbuild.sh              # local dry-run, no release needed
# ...wait for CI to attach AppImages to the v1.8.10 release...
scripts/publish-aur.sh --both         # AUR push for both packages
```

Tasks touching release tooling: verify against current
files -- don't trust older docs referencing `create-app-bundle.sh`,
`appcast*.xml`, Sparkle signing, or old macOS GitHub Actions
workflow. Removed.