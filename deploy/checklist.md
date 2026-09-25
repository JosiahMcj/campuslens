# Deployment checklist — Golden Eagle AI Cabinet

One pass, in order, for a new production host. Everything is environment or
files under the deploy directory; no code changes per deployment.
`/opt/golden-eagle-cabinet` below is the deploy location — substitute your
own, and edit the same path into the plist/unit files.

## 1. Host and domain

- [ ] DNS A/AAAA record for the public hostname points at this machine, and
      ports 80 and 443 reach it (Caddy needs both for the ACME challenge).
- [ ] The hostname is exported as `CABINET_DOMAIN` where the proxy runs:
      `CABINET_DOMAIN=cabinet.example.edu`.
- [ ] Python 3.12 and Node 22 on the host; `make setup` run once in the
      deploy directory; `make check` green there.

## 2. Configuration (secrets never in the repo)

- [ ] Env file written, mode 0600, owned by the service user — default
      `/etc/golden-eagle/cabinet.env` (referenced by `CABINET_LOCAL_ENV` in
      the launchd plist and by `EnvironmentFile=` in the systemd unit):
      ```
      CABINET_SECRET_KEY=<from: python3 -c "import secrets; print(secrets.token_hex(32))">
      CABINET_LLM_BASE_URL=...
      CABINET_LLM_MODEL=...
      CABINET_LLM_LABEL=live model
      CABINET_LLM_API_KEY=...
      ```
      Generate `CABINET_SECRET_KEY` fresh per host; it signs the session
      cookies, so rotating it logs everyone out.
- [ ] `CABINET_BIND` and `CABINET_TRUSTED_PROXY` set in the service
      definition (defaults `127.0.0.1:8910` and `127.0.0.1` — the proxy on
      the same host). Bind a public interface only if you mean to serve
      without the proxy.
- [ ] `make check-config` (with the env file exported or via
      `CABINET_LOCAL_ENV`) shows every expected variable set.

## 3. Build and service

- [ ] `make build` — `ui/dist` written.
- [ ] Service installed and running: `deploy/launchd/com.goldeneagle.cabinet.plist`
      (macOS) or `deploy/systemd/cabinet.service` (Linux). `make serve` is
      the same command by hand, for a foreground check.
- [ ] `curl -fs http://127.0.0.1:8910/ready` answers `{"ready": true}` —
      it checks the database, migrations, the secret, the built UI, and the
      golden replay run, and names what is missing on 503.

## 4. First users

- [ ] `make bootstrap-admin EMAIL=admin@example.edu` — the generated
      password prints exactly once; hand it over a secure channel.
- [ ] `make institution NAME="Two Rivers College" SLUG=two-rivers` for the
      first real institution, then `make user EMAIL=... ROLE=admin
      INSTITUTION=two-rivers` for its admin.
- [ ] Log in through the UI and change nothing until step 6 passes.

## 5. HTTPS

- [ ] Proxy running: `CABINET_DOMAIN=... caddy run --config deploy/Caddyfile`
      (validate first with `caddy validate`), or nginx with
      `deploy/nginx.conf` after `certbot --nginx -d <domain>`.
- [ ] `curl -sI https://$CABINET_DOMAIN/` answers 200 with
      `Strict-Transport-Security` and `Content-Security-Policy` headers, and
      the certificate is the auto-issued one (browser padlock).
- [ ] `curl -sI http://$CABINET_DOMAIN/` redirects to HTTPS (Caddy does this
      itself; the nginx sample has the redirect block).

## 6. Backups

- [ ] Backup timer installed: `deploy/launchd/com.goldeneagle.cabinet-backup.plist`
      or `deploy/systemd/cabinet-backup.timer` (daily 03:17, keeps 14).
- [ ] One manual run: `deploy/backup.sh` — the new `var/backups/<timestamp>/`
      carries a `manifest.json` and the command reported hashes verified.
- [ ] **Restore drill** (do it once before you need it):
      `make stop` (or stop the service), `make restore
      FROM=var/backups/<timestamp>`, start again, confirm `/ready` is 200
      and a login works. The pre-restore state is moved aside as
      `*.pre-restore-*`, never deleted — remove those only when the drill
      is confirmed good.

## 7. Where things are

- Access log: JSON lines on the service's stdout — `journalctl -u cabinet -f`
  (systemd) or `/opt/golden-eagle-cabinet/var/api.access.log` (launchd).
  Every response carries `X-Request-ID`; that id is the join key into the
  access log. Startup errors: `api.error.log` / the same journal.
- Durable state: `var/cabinet.db` + `var/data/` (both inside every backup).
- Backup log: `var/backup.log` / `journalctl -u cabinet-backup`.

## 8. Rotating the model key

1. Write the new key into the env file (`CABINET_LLM_API_KEY`, or the file
   `CABINET_LLM_API_KEY_FILE` points at).
2. Restart the service: `launchctl kickstart -k gui/$(id -u)/com.goldeneagle.cabinet`
   or `sudo systemctl restart cabinet`. In-flight sessions survive (the
   session secret did not change); a request mid-restart is retried by the
   UI's poll.
3. Confirm: ask a question in the UI; the briefing carries the provider
   label and the access log shows a 200 for `POST /api/ask`.

## 9. Taking the service down safely

- Planned stop: `sudo systemctl stop cabinet` or
  `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.goldeneagle.cabinet.plist`.
  SIGTERM shuts uvicorn down gracefully (in-flight requests finish, up to
  10 s). Never `kill -9`: SQLite is safe against it, but a request then
  dies mid-flight.
- Maintenance window: stop the app service, leave the proxy up (it answers
  502), take a manual `deploy/backup.sh` first if data changed.
- Decommission: after the final backup, export every institution's audit
  chain (`.venv/bin/python -m cabinet.audit export var/cabinet.db out.jsonl
  --institution <slug>`) and archive it with the backup before wiping.
