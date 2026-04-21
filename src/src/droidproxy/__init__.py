"""DroidProxy Linux port.

A Python re-implementation of the macOS DroidProxy app: runs the upstream
`cli-proxy-api-plus` Go binary on port 8318 and exposes an HTTP proxy on
port 8317 that injects thinking / reasoning / service_tier fields into
Factory Droid requests.
"""

__version__ = "1.8.15"
