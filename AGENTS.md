# AGENTS.md

## Build & Run

The package lives in `src/` using a standard Python src-layout
(`src/pyproject.toml`, package source at `src/src/droidproxy/`, tests at
`src/tests/`). Run everything from the repo root.

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

Release artifacts are produced by GitHub Actions
(`.github/workflows/linux-release.yml`) on `v*` tags: matrix-built
AppImages (x86_64 + aarch64 + `.zsync`) plus a PyPI-ready wheel/sdist.
There is no local packaging step analogous to the old
`create-app-bundle.sh`.

## Source Of Truth

Treat the following as canonical:

- `src/pyproject.toml` -- dependencies, entry point, package data.
- `src/src/droidproxy/**/*.py` -- runtime code.
- `src/src/droidproxy/resources/config.yaml` -- bundled
  `cli-proxy-api-plus` config (`port: 8318`, localhost binding, Amp
  upstream settings, auth dir).
- `src/src/droidproxy/resources/web/{index.html,styles.css,app.js}` --
  settings UI served at `http://127.0.0.1:8316/`.
- `src/src/droidproxy/resources/factory/{droids,commands}/*.md` --
  bundled Challenger Droid configs copied into `~/.factory/` by
  `install_challenger_droids`.
- `src/packaging/` -- `droidproxy.desktop`, `droidproxy.service`
  (systemd --user), `AppImageBuilder.yml`, `PKGBUILD` (source build) and
  `PKGBUILD-bin` (AppImage-based) for AUR.

There is no other source tree. The old Swift `src/Sources/` and the
macOS-era assets (`create-app-bundle.sh`, `appcast*.xml`, Sparkle
entitlements, `website/`, `graphify-out/`) were removed when this port
became the canonical tree. Do not re-add them.

## Architecture

DroidProxy Linux is a single Python process that owns:

1. `ThinkingProxy` -- an aiohttp HTTP server on `127.0.0.1:8317`
   (`src/src/droidproxy/proxy.py`).
2. `cli-proxy-api-plus` (the upstream Go binary) as a subprocess on
   `127.0.0.1:8318`, managed by `ServerManager`
   (`src/src/droidproxy/backend.py`). The binary is downloaded + SHA-256
   verified on first run by `src/src/droidproxy/binary.py` using a
   version pinned in `PINNED_VERSION`.
3. `WebUI` -- aiohttp app on `127.0.0.1:8316`
   (`src/src/droidproxy/web.py`) serving the settings UI + `/api/*`.
4. `AuthWatcher` -- `watchdog` observer on `~/.cli-proxy-api/` that fans
   OAuth file-changes out over SSE
   (`src/src/droidproxy/auth.py`).
5. Optional `TrayApp` -- GTK `AyatanaAppIndicator3` tray icon
   (`src/src/droidproxy/tray.py`). GTK runs on the main thread; the
   asyncio loop runs on a background daemon thread; menu callbacks
   marshal back via `loop.call_soon_threadsafe(asyncio.ensure_future, coro)`.
6. `TunnelManager` (`src/src/droidproxy/tunnel.py`) -- optional
   `cloudflared tunnel --url` wrapper; never started implicitly.
7. `Updater` (`src/src/droidproxy/updater.py`) -- daily GitHub release
   poll that picks an install-method-aware upgrade path (AppImageUpdate
   / pipx / AUR hint).

The glue lives in:

- `src/src/droidproxy/context.py` -- `AppContext` dataclass owns every
  long-lived singleton.
- `src/src/droidproxy/app.py` -- `run_daemon()` and `run_with_tray()`
  handle asyncio/GTK cohabitation, signal handling, service startup
  order (proxy → backend → auth watcher → web UI → updater).
- `src/src/droidproxy/cli.py` -- argparse entry point
  (`droidproxy = droidproxy.cli:main`).

Typical request flow:

`Client -> :8317 ThinkingProxy -> :8318 cli-proxy-api-plus -> upstream provider`

## Current ThinkingProxy behavior

Byte-stable port of the original Swift `ThinkingProxy`. The injector
(`src/src/droidproxy/injector.py`) edits raw JSON strings via regex so
Anthropic's prompt cache keeps hitting. Never replace the regex
transforms with `json.dumps`, and never reorder keys. The snapshot tests
in `src/tests/test_injector.py` are the contract.

What the proxy does on `POST` JSON bodies:

- Claude adaptive thinking for models containing `opus-4-7` or
  `sonnet-4-6`. Injects `"thinking":{"type":"adaptive"}` and
  `"output_config":{"effort":"..."}`, forces `"stream":true`. Effort
  comes from `Preferences.opus47_thinking_effort` /
  `Preferences.sonnet46_thinking_effort`.
- Codex reasoning for exact models `gpt-5.3-codex` and `gpt-5.4`.
  Injects `"reasoning":{"effort":"..."}`. Effort from
  `Preferences.gpt53_codex_reasoning_effort` /
  `Preferences.gpt54_reasoning_effort`.
- Gemini thinking levels for `gemini-3.1-pro-preview` and
  `gemini-3-flash-preview`. Injects
  `"generationConfig":{"thinkingConfig":{"thinking_level":"..."}}`.
  Level from `Preferences.gemini31_pro_thinking_level` /
  `Preferences.gemini3_flash_thinking_level`.
- Fast mode: injects `"service_tier":"priority"` when
  `/v1/responses` or `/api/v1/responses` is hit with `gpt-5.4` /
  `gpt-5.3-codex` and the matching `*_fast_mode` preference is on and
  the client did not already set `service_tier`.
- Max Budget Mode: when `Preferences.claude_max_budget_mode` is on,
  Sonnet 4.6 requests get classic extended thinking
  (`max_tokens=64000`, `thinking={"type":"enabled","budget_tokens":63999}`,
  `output_config={"effort":"max"}`). Opus 4.7 is intentionally
  unaffected and keeps its adaptive path.
- Gemini on Responses API: rewrites `/v1/responses` →
  `/v1/chat/completions` (and the `/api/` twin) when the JSON body's
  `model` starts with `gemini-`, because CLIProxyAPIPlus does not expose
  Gemini via the Responses API.

What it deliberately does not do:

- Does not strip or normalize model suffixes.
- Does not send `thinking.budget_tokens` to Opus 4.7 (rejected with 400).
- Does not add `anthropic-beta` interleaved-thinking headers (adaptive
  thinking enables interleaving automatically).
- Does not implement any `-thinking-N` suffix-based branching from the
  legacy Swift docs.
- Does not retry on upstream 404 with `/api/` prefix (the old Swift
  helper was dead code).

### Amp routing

Port of the Swift Amp passthrough:

- `/auth/cli-login` and `/api/auth/cli-login` → 302 to
  `https://ampcode.com/...` (strips the `/api/` prefix).
- `/provider/*` is rewritten to `/api/provider/*`.
- Any non-`/api/provider/*` and non-`/v1/*` / non-`/api/v1/*` path is
  treated as an Amp management request and forwarded to
  `https://ampcode.com` with Location and cookie-domain rewrites so the
  browser stays on localhost.

## Auth And Providers

Provider keys exposed in the UI: `claude`, `codex`, `gemini`.

Auth data lives in `~/.cli-proxy-api/` as JSON files, managed by the Go
binary. `AuthManager` reads:

- `type`
- `email`
- `login`
- `expired` (parsed as ISO-8601 with or without fractional seconds)
- `disabled`

Behavior to preserve:

- Multiple accounts per provider.
- Per-account disable/enable via the `disabled` field.
- The last enabled account for a provider cannot be disabled (guard
  lives in `AuthManager.toggle_disabled`).
- Provider-level toggles in the web UI are separate from per-account
  disable flags.
- Provider-level disable writes `oauth-excluded-models` into
  `~/.cli-proxy-api/merged-config.yaml` (generated by
  `ServerManager._write_merged_config`, 0600 perms, atomic rename).
- `cli-proxy-api-plus` hot-reloads `merged-config.yaml`, so provider
  enable/disable never restarts the subprocess.
- `AuthWatcher` (`watchdog.Observer`) rescans `~/.cli-proxy-api/` with
  ~0.5 s debounce and pushes full snapshots over
  `GET /api/auth/stream` (SSE).

## Preferences

`src/src/droidproxy/prefs.py` is a TOML-backed replacement for
`AppPreferences.swift`. The file lives at
`$XDG_CONFIG_HOME/droidproxy/config.toml`.

Keys + defaults (must match the macOS Swift defaults -- changing these
invalidates user installs):

| Key | Default | Valid |
|---|---|---|
| `opus47_thinking_effort` | `xhigh` | `low/medium/high/xhigh/max` |
| `sonnet46_thinking_effort` | `high` | `low/medium/high/max` |
| `gpt53_codex_reasoning_effort` | `high` | `low/medium/high/xhigh` |
| `gpt54_reasoning_effort` | `high` | `low/medium/high/xhigh` |
| `gemini31_pro_thinking_level` | `high` | `low/medium/high` |
| `gemini3_flash_thinking_level` | `high` | `minimal/low/medium/high` |
| `gpt53_codex_fast_mode` | `false` | bool |
| `gpt54_fast_mode` | `false` | bool |
| `claude_max_budget_mode` | `false` | bool |
| `allow_remote` | `false` | bool |
| `secret_key` | `""` | string |
| `oled_theme` | `false` | bool |
| `enabled_providers` | `{claude: true, codex: true, gemini: true}` | dict[str, bool] |

Writes are atomic via `tempfile` + `os.replace`. `PreferencesStore` is a
thread-safe singleton; always go through `get_store()` so the web UI,
proxy, and tray see the same state.

## Key Files

| File | Role |
|---|---|
| `src/src/droidproxy/cli.py` | argparse entry point (`droidproxy` command), subcommands (`tray`, `daemon`, `install-droids`, `install-models`, `install-binary`, `doctor`, `check-update`, `paths`). |
| `src/src/droidproxy/app.py` | Daemon / tray orchestrator, startup order, SIGTERM handling, asyncio/GTK thread split. |
| `src/src/droidproxy/context.py` | `AppContext` dataclass that owns every singleton (`prefs`, `server`, `proxy`, `auth_manager`, `auth_watcher`, `tunnel`, `updater`). |
| `src/src/droidproxy/proxy.py` | aiohttp HTTP proxy on port 8317 (inbound path rewrites, Amp passthrough, streaming passthrough, upstream forwarder). |
| `src/src/droidproxy/injector.py` | Pure, unit-testable JSON field surgery used by the proxy. Regex identical to Swift. |
| `src/src/droidproxy/backend.py` | `ServerManager` -- spawns `cli-proxy-api-plus`, captures logs into a ring buffer, runs `-claude-login` / `-codex-login` / `-login` with the same stdin-nudging timings as Swift, kills orphans via `psutil`. |
| `src/src/droidproxy/binary.py` | Downloads + SHA-256-verifies the pinned upstream release from `router-for-me/CLIProxyAPIPlus`. `PINNED_VERSION` is bumped by `.github/workflows/update-cliproxyapi-linux.yml`. |
| `src/src/droidproxy/auth.py` | `AuthManager` + `AuthWatcher` -- watchdog-backed rescan of `~/.cli-proxy-api/*.json` with SSE broadcast. |
| `src/src/droidproxy/prefs.py` | `PreferencesStore` with atomic TOML persistence and a `get_store()` singleton. |
| `src/src/droidproxy/web.py` | aiohttp settings UI + `/api/*` endpoints (`status`, `prefs`, `auth`, `server`, `logs`, `factory/models`, `tunnel`, `droids`). |
| `src/src/droidproxy/tray.py` | `AyatanaAppIndicator3` tray menu. `gi` is imported lazily inside `run()` so `daemon` mode works without GTK. |
| `src/src/droidproxy/tunnel.py` | Optional `cloudflared` wrapper. Parses `https://*.trycloudflare.com` from stderr/stdout. |
| `src/src/droidproxy/updater.py` | GitHub release poller + install-method router (AppImage / pipx / AUR / source). No Sparkle equivalent. |
| `src/src/droidproxy/installer.py` | `install_challenger_droids` (copies `.md` into `~/.factory/`) and `install_factory_custom_models` (merges DroidProxy entries into `~/.factory/settings.json`, preserves other keys, scrubs legacy + `custom:CC:*` IDs). |
| `src/src/droidproxy/paths.py` | XDG-aware path helpers (`config_dir`, `data_dir`, `state_dir`, `auth_dir`, `cli_proxy_api_binary`, resource lookups). |
| `src/src/droidproxy/resources/config.yaml` | Bundled CLIProxyAPIPlus config template. Merged with user prefs into `~/.cli-proxy-api/merged-config.yaml` at runtime. |
| `src/src/droidproxy/resources/web/` | Vanilla JS settings page served by `WebUI`. |
| `src/tests/` | 95 tests (pytest + pytest-asyncio). `test_injector.py` is the prompt-caching parity contract -- treat any failure there as a production bug. |

## Factory Integration

Two helpers in `installer.py`, both mirrored as web endpoints and CLI
subcommands:

| Action | CLI | Endpoint | Effect |
|---|---|---|---|
| Apply DroidProxy models | `droidproxy install-models` | `POST /api/factory/models/apply` | Merges `DROID_PROXY_MODELS` into `~/.factory/settings.json`, scrubs `custom:droidproxy:*` + `custom:droidproxy:opus-4-6` (legacy) + `custom:CC:*`, re-indexes after user-added models, skips disabled-provider models. Atomic write. |
| Install Challenger Droids | `droidproxy install-droids` | `POST /api/droids/install` | Copies the bundled `.md` files into `~/.factory/droids/` and `~/.factory/commands/`. |

Both respect the provider toggle state in `Preferences.enabled_providers`.

## Conventions

- Use the standard library `logging` module (`log = logging.getLogger(__name__)`),
  never `print()` in runtime modules. `ThinkingProxy` additionally mirrors
  key events to `/tmp/droidproxy-debug.log` for parity with the macOS
  `ThinkingProxy.fileLog`.
- Keep the `src/src/droidproxy/injector.py` regex + semantics
  byte-identical to the Swift original. The comment block at the top of
  `injector.py` documents the contract. Any refactor that changes its
  output bytes is a production regression (prompt caching breaks).
- Do not re-serialise JSON request bodies in the proxy path; always
  edit the raw string.
- Tray code is optional. Nothing else in the package may import `gi` /
  `gtk` at module scope -- tests run on headless CI. If you need GTK,
  put it inside a function and guard with `TrayUnavailableError`.
- Local backend traffic stays on `127.0.0.1:8318`. Flip `allow-remote`
  only via the `remote-management` section of the merged config, which
  is driven by the `allow_remote` / `secret_key` prefs.
- Always run `ruff check src/src src/tests` and
  `python -m pytest src/tests` before committing.
- Preserve the `src/` src-layout. Package code lives at
  `src/src/droidproxy/`; do not flatten.
- Every preference change goes through `PreferencesStore.update` or
  `set_provider_enabled`, which handle validation + atomic persist.
  Never write `config.toml` directly.

## Release Notes For Agents

Release pipeline for the Linux port:

- CI: `.github/workflows/linux-release.yml` on `v*` tags. Builds
  x86_64 + aarch64 AppImages (with `.zsync`), publishes the wheel /
  sdist artifacts, and attaches everything to the GitHub release via
  `softprops/action-gh-release`.
- Upstream bump automation: `.github/workflows/update-cliproxyapi-linux.yml`
  runs every 12 h, checks for a new `router-for-me/CLIProxyAPIPlus`
  release, and opens a PR that bumps `PINNED_VERSION` in
  `src/src/droidproxy/binary.py`. The binary itself is not vendored --
  it downloads at first launch with a SHA-256 check against upstream
  `checksums.txt`.
- Distribution: AppImage (primary), PyPI wheel (`pipx install droidproxy`),
  and AUR (`droidproxy` source + `droidproxy-bin` AppImage). PKGBUILDs
  live in `src/packaging/`. Before pushing to AUR, populate real
  `sha256sums` with `updpkgsums` against the GitHub release, regenerate
  `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO`, and push to the
  package-specific `ssh://aur@aur.archlinux.org/<pkgname>.git` repo.
- There is no Sparkle, notarization, code-signing, or `.icns` build
  step. The updater opens AppImageUpdate (if installed) for AppImage
  users or prints an install-method-specific hint otherwise.
- Version string lives in two places: `src/src/droidproxy/__init__.py`
  (`__version__`) and `src/pyproject.toml` (`version = "..."`). Keep
  them in sync when tagging.

Any task touching release tooling should verify against the current
files -- do not trust older documents that reference `create-app-bundle.sh`,
`appcast*.xml`, Sparkle signing, or the old macOS GitHub Actions
workflow. Those have been removed.
