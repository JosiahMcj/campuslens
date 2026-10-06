# Golden Eagle AI Cabinet — one-command entrypoints.
# Toolchain: a user-local bin directory may hold python3.12, node, and npm;
# it is prepended to PATH if present and never required. CI provides them on PATH.

export PATH := $(HOME)/.local/bin:$(PATH)

PYTHON ?= python3.12
VENV := .venv
PYBIN := $(VENV)/bin

.PHONY: setup setup-python setup-ui \
	lint lint-python lint-ui \
	typecheck typecheck-python typecheck-ui \
	test test-python test-ui \
	check audit check-config bootstrap-admin institution user import-ethos api ui stop record-golden \
	migrate backup restore purge-deleted build serve school-data school-check

setup: setup-python setup-ui

setup-python:
	$(PYTHON) -m venv $(VENV)
	$(PYBIN)/pip install --upgrade pip
	$(PYBIN)/pip install -r backend/requirements-dev.txt -e backend

setup-ui:
	npm --prefix ui ci

lint: lint-python lint-ui

lint-python:
	cd backend && ../$(PYBIN)/ruff check .

lint-ui:
	npm --prefix ui run lint

typecheck: typecheck-python typecheck-ui

typecheck-python:
	cd backend && ../$(PYBIN)/mypy src tests

typecheck-ui:
	npm --prefix ui run typecheck

test: test-python test-ui

test-python:
	cd backend && ../$(PYBIN)/pytest -q

test-ui:
	npm --prefix ui run test

check: lint typecheck test

# Dependency audits: known-vulnerability scan of the pinned runtime
# requirements (pip-audit, from requirements-dev) and the UI's production
# dependencies (npm audit --omit=dev). Both must be clean; any accepted
# advisory is documented in docs/SECURITY.md.
audit:
	cd backend && ../$(PYBIN)/pip-audit -r requirements.txt
	npm --prefix ui audit --omit=dev

# Which CABINET_* variables are set — values redacted, safe to paste.
check-config:
	$(PYBIN)/python -m cabinet.config

# Create the bootstrap institution and its first admin (refuses once one
# exists) with a generated password printed exactly once; create institutions
# with `make institution` and further users with `make user`.
# Example: make bootstrap-admin EMAIL=admin@example.edu
#          make institution NAME="Two Rivers College" SLUG=two-rivers
#          make user EMAIL=exec@example.edu ROLE=executive INSTITUTION=two-rivers
bootstrap-admin:
	@if [ -z "$(EMAIL)" ]; then echo "usage: make bootstrap-admin EMAIL=..." >&2; exit 2; fi
	$(PYBIN)/python -m cabinet.users bootstrap-admin --email "$(EMAIL)"

institution:
	@if [ -z "$(NAME)" ] || [ -z "$(SLUG)" ]; then echo "usage: make institution NAME=... SLUG=..." >&2; exit 2; fi
	$(PYBIN)/python -m cabinet.institutions add --name "$(NAME)" --slug "$(SLUG)"

user:
	@if [ -z "$(EMAIL)" ] || [ -z "$(ROLE)" ]; then echo "usage: make user EMAIL=... ROLE=admin|executive|staff|reviewer [INSTITUTION=slug]" >&2; exit 2; fi
	@if [ -n "$(INSTITUTION)" ]; then \
		$(PYBIN)/python -m cabinet.users add --email "$(EMAIL)" --role "$(ROLE)" --institution "$(INSTITUTION)"; \
	else \
		$(PYBIN)/python -m cabinet.users add --email "$(EMAIL)" --role "$(ROLE)"; \
	fi

# Import one term from the institution's Ellucian Ethos Integration API
# (docs/ELLUCIAN.md): fetch the mapped resources at the institution's edge,
# pseudonymise student and advisor ids with the keyed hash, write the export
# to var/exports/<slug>-<term>-<timestamp>-<suffix>.json (0600), validate it
# exactly like the admin UI upload, and store it inactive until an admin
# activates it; the export copy is then removed (kept only on DRY_RUN=1).
# Requires CABINET_ETHOS_BASE_URL, CABINET_ETHOS_API_KEY_FILE, and
# CABINET_PSEUDONYM_KEY_FILE (environment or cabinet.local.env).
# Example: make import-ethos INSTITUTION=bootstrap TERM=202720
#          make import-ethos INSTITUTION=bootstrap TERM=202720 DRY_RUN=1
import-ethos:
	@if [ -z "$(INSTITUTION)" ] || [ -z "$(TERM)" ]; then echo "usage: make import-ethos INSTITUTION=<slug> TERM=<code> [DRY_RUN=1]" >&2; exit 2; fi
	$(PYBIN)/python -m cabinet.ellucian import --institution "$(INSTITUTION)" --term "$(TERM)" $(if $(DRY_RUN),--dry-run,)

# Apply pending schema migrations to var/cabinet.db (the app also applies
# known pending migrations at startup and refuses a version it does not
# know; this target is the explicit, inspectable path).
migrate:
	$(PYBIN)/python -m cabinet.migrations

# Snapshot var/cabinet.db (via the SQLite backup API, never a file copy of
# the live database) and the dataset files in var/data/ into
# var/backups/<UTC timestamp>/ with a sha256 manifest, verified after the
# write. Nothing in a backup is ever deleted.
backup:
	$(PYBIN)/python -m cabinet.backup create

# Restore a backup created by `make backup`. The API and UI must be stopped
# first; existing live files are moved aside (never deleted) as
# *.pre-restore-<timestamp>. The backup's hashes are verified before and
# after the restore.
restore:
	@if [ -z "$(FROM)" ]; then echo "usage: make restore FROM=var/backups/<timestamp>" >&2; exit 2; fi
	@for p in api ui; do \
		if [ -f var/$$p.pid ] && kill -0 "$$(cat var/$$p.pid)" 2>/dev/null; then \
			echo "restore: the $$p server is running (pid $$(cat var/$$p.pid)); stop it first (make stop)" >&2; \
			exit 2; \
		fi; \
	done
	$(PYBIN)/python -m cabinet.backup restore "$(FROM)"

# Hard-delete datasets soft-deleted more than 30 days ago (the retention
# rule; --days N overrides). Rows and files are removed; nothing else.
purge-deleted:
	$(PYBIN)/python -m cabinet.datasets purge-deleted

# Start the API in the background; pid in var/api.pid, log in var/api.log.
# Binds 127.0.0.1:8910 by default; CABINET_BIND is host or host:port, and
# must be set explicitly to bind anything else (required in production — the
# app fails closed without it).
# `make api REPLAY=1` starts it with CABINET_PROVIDER=replay (recorded responses
# from var/replay/ or the golden run in data/golden/, no network); otherwise
# CABINET_PROVIDER passes through from the environment, defaulting to chat (the
# CABINET_LLM_* variables or cabinet.local.env — see RUNBOOK.md). After starting,
# /health is polled for up to 8 s: "api started" prints only on success;
# otherwise the last 20 log lines are shown, the pid file is removed, and the
# target exits non-zero.
api:
	@mkdir -p var
	@if [ -f var/api.pid ] && kill -0 "$$(cat var/api.pid)" 2>/dev/null; then \
		echo "api already running (pid $$(cat var/api.pid))"; \
	else \
		if [ -n "$(REPLAY)" ]; then provider=replay; else provider=$${CABINET_PROVIDER:-chat}; fi; \
		bind=$${CABINET_BIND:-127.0.0.1}; \
		case "$$bind" in *:*) host=$${bind%:*}; port=$${bind##*:};; *) host=$$bind; port=8910;; esac; \
		CABINET_BIND=$$bind CABINET_PROVIDER=$$provider nohup $(PYBIN)/uvicorn cabinet.app:app --host $$host --port $$port \
			> var/api.log 2>&1 & echo $$! > var/api.pid; \
		ok=0; \
		for i in $$(seq 1 80); do \
			if curl -fs http://127.0.0.1:$$port/health > /dev/null 2>&1; then ok=1; break; fi; \
			kill -0 "$$(cat var/api.pid)" 2>/dev/null || break; \
			sleep 0.1; \
		done; \
		if [ "$$ok" = "1" ]; then \
			echo "api started (pid $$(cat var/api.pid), provider $$provider, bind $$host:$$port), http://127.0.0.1:$$port"; \
		else \
			pid=$$(cat var/api.pid); \
			echo "api failed to start (pid $$pid); last 20 lines of var/api.log:"; \
			tail -n 20 var/api.log; \
			kill "$$pid" 2>/dev/null || true; \
			rm -f var/api.pid; \
			exit 1; \
		fi; \
	fi

# Build the production UI: tsc -b + vite build into ui/dist (content-hashed
# assets under ui/dist/assets/, index.html at the root). `make serve` serves
# this directory; the Vite dev server (make ui) does not need it.
build:
	npm --prefix ui run build

# Serve the whole app — API and the built UI from ui/dist — in production
# mode from one uvicorn process. Backgrounds deploy/run-production.sh,
# which is also what the launchd plist and the systemd unit exec, so the
# production flags live in one place: CABINET_ENV=production, one worker
# (the rate limiters and caches are in-process — see the script header),
# JSON access logs on stdout with request ids, proxy headers trusted only
# from CABINET_TRUSTED_PROXY (default 127.0.0.1), graceful shutdown on
# SIGTERM. Bind from CABINET_BIND (host or host:port, default
# 127.0.0.1:8910). Requires CABINET_SECRET_KEY (32+ bytes) and, for live
# answers, the CABINET_LLM_* configuration — both from the environment or
# the env file CABINET_LOCAL_ENV points at (never the repo). Shares
# var/api.pid with `make api`, so `make stop` stops it and both targets
# refuse to double-start. Fails closed: without the secret the app exits
# and this target reports the log tail.
serve:
	@mkdir -p var
	@if [ -f var/api.pid ] && kill -0 "$$(cat var/api.pid)" 2>/dev/null; then \
		echo "api already running (pid $$(cat var/api.pid))"; \
	else \
		bind=$${CABINET_BIND:-127.0.0.1:8910}; \
		case "$$bind" in *:*) host=$${bind%:*}; port=$${bind##*:};; *) host=$$bind; port=8910;; esac; \
		nohup deploy/run-production.sh > var/api.log 2>&1 & echo $$! > var/api.pid; \
		ok=0; \
		for i in $$(seq 1 80); do \
			if curl -fs http://127.0.0.1:$$port/health > /dev/null 2>&1; then ok=1; break; fi; \
			kill -0 "$$(cat var/api.pid)" 2>/dev/null || break; \
			sleep 0.1; \
		done; \
		if [ "$$ok" = "1" ]; then \
			echo "serving production build (pid $$(cat var/api.pid), bind $$host:$$port), http://127.0.0.1:$$port"; \
		else \
			pid=$$(cat var/api.pid); \
			echo "serve failed to start (pid $$pid); last 20 lines of var/api.log:"; \
			tail -n 20 var/api.log; \
			kill "$$pid" 2>/dev/null || true; \
			rm -f var/api.pid; \
			exit 1; \
		fi; \
	fi

# Start the UI dev server on 127.0.0.1:5200 in the background; pid in var/ui.pid,
# log in var/ui.log. After starting, / is polled for up to 8 s: "ui started"
# prints only on success; otherwise the last 20 log lines are shown, the pid
# file is removed, and the target exits non-zero.
ui:
	@mkdir -p var
	@if [ -f var/ui.pid ] && kill -0 "$$(cat var/ui.pid)" 2>/dev/null; then \
		echo "ui already running (pid $$(cat var/ui.pid))"; \
	else \
		nohup sh -c 'cd ui && exec ./node_modules/.bin/vite' \
			> var/ui.log 2>&1 & echo $$! > var/ui.pid; \
		ok=0; \
		for i in $$(seq 1 80); do \
			if curl -fs http://127.0.0.1:5200/ > /dev/null 2>&1; then ok=1; break; fi; \
			kill -0 "$$(cat var/ui.pid)" 2>/dev/null || break; \
			sleep 0.1; \
		done; \
		if [ "$$ok" = "1" ]; then \
			echo "ui started (pid $$(cat var/ui.pid)), http://127.0.0.1:5200"; \
		else \
			pid=$$(cat var/ui.pid); \
			echo "ui failed to start (pid $$pid); last 20 lines of var/ui.log:"; \
			tail -n 20 var/ui.log; \
			kill "$$pid" 2>/dev/null || true; \
			rm -f var/ui.pid; \
			exit 1; \
		fi; \
	fi

# Record the golden run: one live pass per analyst (Enrollment, Student
# Success) through the normal path (gate → provider → validation) with
# recording on. Each validated response is written into the committed
# data/golden/ directory that REPLAY mode falls back to, from the validated
# output itself (never copied from var/replay/). Requires the live model
# (CABINET_LLM_* variables or cabinet.local.env — see RUNBOOK.md) and refuses
# to run while the API is up (both hold the same cabinet.db): stop it first
# with `make stop`.
record-golden:
	@mkdir -p var data/golden
	@if [ -f var/api.pid ] && kill -0 "$$(cat var/api.pid)" 2>/dev/null; then \
		echo "record-golden: the API is running (pid $$(cat var/api.pid)); stop the API first (make stop)" >&2; \
		exit 2; \
	fi
	CABINET_RECORD=1 $(PYBIN)/python -m cabinet.record_golden

# Stop the processes this project started on 8910 and 5200, via pid files in
# var/ — and their child processes (e.g. vite under sh). A pid whose command
# line is not what this project starts (uvicorn cabinet.app:app / vite) is a
# stale, reused pid: the pid file is removed and the process is NOT killed.
# Anything else still listening on those ports is reported with its pid and
# command, never killed: this project only stops what it started.
stop:
	@for p in api ui; do \
		if [ -f var/$$p.pid ]; then \
			pid=$$(cat var/$$p.pid); \
			cmd=$$(ps -p "$$pid" -o command= 2>/dev/null || true); \
			match=0; \
			case "$$p" in \
				api) case "$$cmd" in *"uvicorn cabinet.app:app"*) match=1;; esac;; \
				ui) case "$$cmd" in *vite*) match=1;; esac;; \
			esac; \
			if kill -0 "$$pid" 2>/dev/null && [ "$$match" = "1" ]; then \
				children=$$(pgrep -P "$$pid" 2>/dev/null || true); \
				for child in $$children; do \
					if kill "$$child" 2>/dev/null; then \
						echo "stopped $$p child process (pid $$child)"; \
					fi; \
				done; \
				if kill "$$pid" 2>/dev/null; then \
					echo "stopped $$p (pid $$pid)"; \
				fi; \
				for i in $$(seq 1 50); do \
					kill -0 "$$pid" 2>/dev/null || break; \
					sleep 0.1; \
				done; \
			elif kill -0 "$$pid" 2>/dev/null; then \
				echo "var/$$p.pid points at pid $$pid ('$$cmd'), which is not this project's $$p server — removing the stale pid file, not killing"; \
			fi; \
			rm -f var/$$p.pid; \
		fi; \
	done
	@for port in 8910 5200; do \
		pids=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
		for pid in $$pids; do \
			echo "port $$port is still held by pid $$pid ($$(ps -p $$pid -o command= 2>/dev/null || echo 'unknown command')) — not killed; it was not started from this directory's pid files"; \
		done; \
	done

# Demonstration University, the synthetic school (data/school/README.md).
# school-data generates the whole university at scale 1.0 into
# var/school/school.db (seeded, deterministic, never committed) and then
# runs the checker. school-check re-runs the checker alone: schema,
# realism rules, and every planted fact in data/school/VERIFY.md.
school-data:
	@mkdir -p var/school
	$(PYBIN)/python data/school/generate.py --out var/school/school.db
	$(PYBIN)/python data/school/check.py --db var/school/school.db

school-check:
	$(PYBIN)/python data/school/check.py --db var/school/school.db
