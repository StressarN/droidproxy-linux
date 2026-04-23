# DroidProxy Linux

A Linux port of [DroidProxy](https://github.com/anand-92/droidproxy) -- the
native macOS menu bar app that proxies Claude Code, Codex, and Gemini
subscriptions for Factory Droid. Built on
[CLIProxyAPIPlus](https://github.com/router-for-me/CLIProxyAPIPlus).

## What you get

A Python daemon that spawns `cli-proxy-api-plus` on `127.0.0.1:8318` and a
thinking/reasoning proxy on `127.0.0.1:8317`. A GTK tray icon
(AyatanaAppIndicator3) stays in the system tray and a local web UI on
`http://127.0.0.1:8316` exposes settings, account management, and live
logs. Byte-stable JSON injection preserves Anthropic prompt caching.

Parity with the macOS app:

- OAuth authentication for Claude, Codex, and Gemini, with auto-refresh.
- Claude adaptive thinking (`thinking: {"type":"adaptive"}`) plus per-model
  `output_config.effort` for Opus 4.7 and Sonnet 4.6.
- Codex reasoning effort (`reasoning: {"effort":"..."}`) for `gpt-5.3-codex`
  and `gpt-5.4`.
- Gemini thinking levels for `gemini-3.1-pro-preview` and
  `gemini-3-flash-preview`.
- Max Budget Mode for Sonnet 4.6 (classic extended thinking with
  `budget_tokens: 63999`, `max_tokens: 64000`, `effort: max`). Opus 4.7 is
  unaffected.
- Fast mode `service_tier: priority` on `/v1/responses` for GPT 5.4 and
  GPT 5.3 Codex.
- Amp CLI management passthrough to `https://ampcode.com` with Location
  and cookie-domain rewrites.
- Cloudflared tunnel integration (requires the `cloudflared` binary on the
  host).
- Challenger Droid + slash-command install helper for Factory.

## Requirements

- Linux (x86_64 or aarch64)
- Python 3.11+
- GTK 3 + AyatanaAppIndicator3 runtime (optional; only needed for the tray)
- `xdg-open` for launching the browser during OAuth

On Arch Linux / Omarchy:

```bash
sudo pacman -S python python-gobject gtk3 libayatana-appindicator xdg-utils
```

On Debian / Ubuntu:

```bash
sudo apt-get install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 xdg-utils
```

On Fedora:

```bash
sudo dnf install python3-gobject gtk3 libayatana-appindicator3 xdg-utils
```

## Install

### AppImage (recommended)

Download the AppImage for your architecture from the
[releases page](https://github.com/StressarN/droidproxy-linux/releases/latest):

```bash
chmod +x DroidProxyLinux-x86_64.AppImage
./DroidProxyLinux-x86_64.AppImage
```

The AppImage ships with its own Python runtime and all GTK bindings, so no
system packages are required. In-place updates work via
[AppImageUpdate](https://github.com/AppImage/AppImageUpdate).

### pipx

```bash
pipx install droidproxy
droidproxy
```

Install the `tray` extra if you want the AppIndicator integration:

```bash
pipx install 'droidproxy[tray]'
```

### Arch (AUR)

Two packages are published from this fork:

- `droidproxy-linux-bin` -- uses the AppImage (recommended for most users)
- `droidproxy-linux` -- source build against system Python / GTK

```bash
paru -S droidproxy-linux-bin     # or: droidproxy-linux
```

### Source checkout

```bash
git clone https://github.com/StressarN/droidproxy-linux.git
cd droidproxy-linux/src
python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e '.[tray,dev]'
```

## Usage

```bash
droidproxy                # Start tray + daemon + web UI (default)
droidproxy daemon         # Headless (no tray)
droidproxy doctor         # Health check for paths, GTK, cloudflared
droidproxy install-droids # Copy Challenger Droid configs into ~/.factory/
droidproxy install-binary # (Re)download the cli-proxy-api-plus backend
droidproxy check-update   # Compare installed vs latest release
droidproxy paths          # Print the XDG dirs DroidProxy uses
```

- Settings UI: <http://127.0.0.1:8316/>
- Thinking proxy: <http://127.0.0.1:8317/> (point Factory here)
- Backend dashboard: <http://127.0.0.1:8318/management.html>

## Setup guides

- [`SETUP.md`](SETUP.md) -- use Factory Droid with your subscriptions.
- [`AMP_SETUP.md`](AMP_SETUP.md) -- route Amp CLI through DroidProxy.

## Running as a user service

Copy the systemd unit:

```bash
mkdir -p ~/.config/systemd/user
cp src/packaging/droidproxy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now droidproxy.service
journalctl --user -u droidproxy.service -f
```

## Troubleshooting

- **Tray icon missing on GNOME** -- install the AppIndicator extension (or
  switch to the `droidproxy daemon` mode plus the web UI).
- **Port 8317 or 8318 already in use** -- override with
  `droidproxy --proxy-port 18317 --upstream-port 18318`. Update
  `~/.factory/settings.json` to match.
- **OAuth browser did not open** -- `xdg-open` could not find a handler.
  Copy the auth URL out of the web UI log tail and open it manually.
- **cloudflared not found** -- install from your package manager or
  <https://github.com/cloudflare/cloudflared/releases>.

## Development

```bash
python -m pytest src/tests     # 84 tests covering prefs, injector, proxy,
                               # backend, auth, web UI, tunnel, updater, tray
ruff check src/src src/tests   # lint
```

## License

MIT. See [`LICENSE`](LICENSE).
