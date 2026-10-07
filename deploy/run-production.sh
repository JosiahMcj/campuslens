#!/bin/sh
# Run CampusLens in production. `make serve` backgrounds this script;
# the launchd plist and the systemd unit exec it directly, so the production
# flags live in exactly one place.
#
# Configuration is environment only (never this file):
#   CABINET_BIND           host or host:port to listen on (default
#                          127.0.0.1:8910); must be set explicitly in
#                          production — the app fails closed otherwise
#   CABINET_TRUSTED_PROXY  the only address(es) whose X-Forwarded-For is
#                          believed (default 127.0.0.1 — the reverse proxy on
#                          the same host); uvicorn forwards this to
#                          --forwarded-allow-ips
#   CABINET_ENV            forced to production here
#   CABINET_PROVIDER, CABINET_LLM_*, CABINET_SECRET_KEY, ...  pass through
#   CABINET_LOCAL_ENV      env file to load (see cabinet.provider.load_local_env)
#
# Workers is pinned to 1 on purpose: the rate limiters, login lockout, and
# briefing caches are in-process (docs/SECURITY.md); a second worker would
# split them. Scale at the proxy or raise the in-process limits instead.
#
# Access logs are one JSON line per request on stdout from the app itself
# (cabinet.accesslog, production mode), so uvicorn's own access log is off.
# SIGTERM shuts down gracefully: uvicorn finishes in-flight requests for up
# to --timeout-graceful-shutdown seconds.

set -eu

cd "$(dirname "$0")/.."

bind=${CABINET_BIND:-127.0.0.1:8910}
case "$bind" in
    *:*) host=${bind%:*}; port=${bind##*:} ;;
    *)   host=$bind;     port=8910 ;;
esac
trusted=${CABINET_TRUSTED_PROXY:-127.0.0.1}

export CABINET_ENV=production
export CABINET_BIND=$bind

exec .venv/bin/uvicorn cabinet.app:app \
    --host "$host" --port "$port" \
    --workers 1 \
    --no-access-log \
    --proxy-headers --forwarded-allow-ips "$trusted" \
    --timeout-graceful-shutdown 10
