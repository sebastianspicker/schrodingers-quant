# Local rehearsal record

Run on macOS with Docker (OrbStack backend), `docker compose` v5.1.2, image
`freqtradeorg/freqtrade:2026.8@sha256:4d23160b…ab4` (already local, no pull).
Date: 2026-09-24. This rehearses what a laptop can rehearse; it is not a
substitute for the 14-day soak on the actual Debian 13 host (see
`ops/soak-checklist.md`). See the restart-policy note in §5 for why that
distinction matters.

No real secrets were used: `config/local/rehearsal-secrets.json` (temporary,
removed afterward) held dummy API-server credentials, referenced below as
`$API_USER`/`$API_PASS`, and Telegram disabled (kept off to avoid any
outbound call). `compose.vps.yaml` (temporary, removed afterward) was a copy
of `compose.vps.example.yaml` with its command pointed at that dummy secrets
file instead of `config/local/vps.json` + `config/local/secrets.json`.

All facts below are actual command output from this session, not expected
behavior.

## 1. Bring-up with the VPS overlay and COMPOSE_FILE

`COMPOSE_FILE=compose.yaml:compose.vps.yaml docker compose up -d` (no `-f`
flags, the mechanism `ops/backup.sh`, `ops/restore.sh` and
`schrodingers-quant.service` all rely on) started the container with the API
port published:

```
schrodingers-quant-freqtrade-1   ...   Up   127.0.0.1:8080->8080/tcp
```

`docker compose config` confirmed the merged port mapping:

```
    ports:
      - mode: ingress
        host_ip: 127.0.0.1
        target: 8080
```

The COMPOSE_FILE-via-.env mechanism itself (Compose auto-loading `.env` for
`COMPOSE_FILE` and both compose files with no `-f` flags at all) was verified
separately in an isolated scratch directory, to avoid creating a `.env` file
directly in the project directory during the test. The behavior is otherwise
identical: Compose resolves `COMPOSE_FILE` from either the process
environment or `.env`, both relative to the current working directory. That
is why `ops/backup.sh`/`ops/restore.sh`'s `dc()` helper `cd`s into the repo
root before calling `docker compose` rather than using `--project-directory`
(confirmed `--project-directory` does *not* change where Compose looks for
`.env` or default compose files; see the bug note in §5).

## 2. API endpoints

```
$ curl http://127.0.0.1:8080/api/v1/ping
{"status":"pong"}                                                  HTTP 200

$ curl -u "$API_USER:$API_PASS" http://127.0.0.1:8080/api/v1/health
{"last_process":null,"last_process_ts":null,"bot_start":"2026-09-24T15:58:56Z",...}   HTTP 200
# (last_process is null here: the bot had not yet completed a processing
# cycle while in the STOPPED state, expected, see §4)

$ curl http://127.0.0.1:8080/api/v1/health          # no credentials
{"detail":"Unauthorized"}                                          HTTP 401

$ curl -u "$API_USER:$API_PASS" -X POST http://127.0.0.1:8080/api/v1/stopentry
{"status":"starting bot with trader in paused state, no entries will occur. Run /start to enable entries."}  HTTP 200
```

## 3. `ops/health_ping.py` against the live container

A tiny stdlib HTTP server on `127.0.0.1:9099` stood in for the dead-man's-switch
(`HC_PING_URL=http://127.0.0.1:9099/ping/rehearsal`). All four decision paths
were exercised against the real running (or intentionally misconfigured)
container, with the server's own access log as independent evidence of
delivery:

| Scenario | Command | Exit | Server log line |
| --- | --- | --- | --- |
| Healthy | `health_ping.py` (bot started, `/start`ed) | `0` (EXIT_OK) | `GET /ping/rehearsal ... 200` |
| Stale | `health_ping.py --max-age-seconds 0` | `1` (EXIT_UNHEALTHY) | `POST /ping/rehearsal/fail body=b'last process was 0s ago, exceeds 0s threshold' ... 200` |
| Unreachable API | `health_ping.py --base-url http://127.0.0.1:9 --timeout 2` | `2` (EXIT_UNREACHABLE) | `POST /ping/rehearsal/fail body=b'could not reach ...Connection refused' ... 200` |
| Alert-relay mode | `health_ping.py --fail "sq-backup.service failed"` | `0` (EXIT_OK; the *notification* succeeded) | `POST /ping/rehearsal/fail body=b'sq-backup.service failed' ... 200` |

An earlier pass used Python's plain `http.server` module, which only
implements `GET`/`HEAD` and returned `501` for the `POST .../fail` calls,
correctly producing exit `3` (EXIT_PING_FAILED: the local decision was made,
but delivery of that decision failed). That is genuine, correct behavior of
`health_ping.py`, not a bug. It was replaced with a minimal
`BaseHTTPRequestHandler` subclass that also implements `do_POST` (real
healthchecks.io accepts POST) to additionally exercise the success-delivery
path for the fail cases, shown in the table above.

Disk check: `disk usage 62.3%` on the development machine's filesystem
(below the default 85% threshold) throughout. The disk-full path is only
exercised via `tests/ops/test_health_ping.py`'s unit tests and the D5 drill
in the soak checklist, not locally against a real full disk.

## 4. `ops/backup.sh` then `ops/restore.sh`

```
$ ops/backup.sh
... container running: snapshotting via docker compose exec
... integrity_check ok
... excluded config/local (no RESTIC_REPOSITORY configured)
... backup written to .../user_data/runtime/backups/20260924T160311Z
... done                                                    exit 0

$ ops/restore.sh --local user_data/runtime/backups/20260924T160311Z
... checking integrity of the staged snapshot before touching the live database
... staged snapshot integrity_check ok
... stopping the freqtrade service
... moved the current database aside to .../dry-run.sqlite.pre-restore.20260924T160319Z
... restored snapshot copied to .../dry-run.sqlite
... checking integrity of the restored live database
... restored database integrity_check ok
... starting the freqtrade service (bot state will be 'stopped' per initial_state)
... done. The bot is running but NOT trading (initial_state: stopped).      exit 0
```

Both `PRAGMA integrity_check` calls (staged copy and post-restore live copy)
reported `ok`. The prior database was renamed aside, not deleted. After the
restore, `docker compose ps` showed the container `Starting`/`Started`
(reusing the existing config), not `Recreate`, confirming `dc()`'s `cd`-based
Compose file discovery (§1) picked up the same overlay, so the API port stayed
published across the restore. The health endpoint confirmed the bot state:

```
$ curl -u "$API_USER:$API_PASS" http://127.0.0.1:8080/api/v1/health
{"last_process":null,"last_process_ts":null,...}
$ docker compose logs freqtrade | grep "Changing state"
freqtrade-1  | ... Changing state to: STOPPED
```

A work-in-progress `config/live.json` file, present in `config/` at
rehearsal time, was bundled correctly and `config/local` was excluded,
confirming the restic-gating logic in `ops/backup.sh`.

## 5. Container kill and restart policy

`docker kill <container>` was run twice (fresh container each time). Both
times the container reached `Exited (137)` and **did not** restart on its own
within 40+ seconds, despite `compose.yaml`'s `restart: unless-stopped`.
`docker compose up -d` afterward did bring it back immediately, reusing the
existing configuration (port mapping intact).

**Resolved 2026-09-24:** this is documented Docker behavior, not an OrbStack
quirk. A container stopped via `docker kill` counts as a manual stop, so
`restart: unless-stopped` deliberately does not restart it. Killing the
Freqtrade process *inside* the container instead (see soak-checklist D1)
did produce a restart, with `RestartCount=1`.

The original observation: `docker events` showed `kill` → `die` with no
following `start` event even after 40+ seconds, and `RestartPolicy`
inspected as `{unless-stopped 0}`, which is correct. That ruled out a
misconfiguration; the remaining question, since resolved, was whether this
was Docker's documented behavior or an OrbStack-specific quirk.

A second, unrelated bug was found and fixed during this rehearsal:
`ops/backup.sh` and `ops/restore.sh` originally used `docker compose
--project-directory "$repo_root" ...`. `--project-directory` does **not**
change where Compose looks for `.env`/default compose files (confirmed by
reproducing the failure: the first `restore.sh` run recreated the container
using only the bare `compose.yaml`, silently dropping the VPS overlay and
its published port). Fixed by `cd`-ing into the repo root inside `dc()`
instead; re-ran the full backup/restore cycle (§4) to confirm the fix.

## 6. Teardown

```
docker compose down          # container + network removed
```

Confirmed afterward: `docker compose ps -a` empty; `config/local/` contains
only `.gitkeep`; `compose.vps.yaml` and `config/local/rehearsal-secrets.json`
removed; `user_data/runtime/` contains only `.gitkeep` (no `dry-run.sqlite`
existed before this rehearsal, checked first, so the one created during this
session, its `-shm`/`-wal` files, the two `dry-run.sqlite.pre-restore.*`
files from the restore drill, and `user_data/runtime/backups/` were all
removed rather than left behind); `git status --short` shows no tracked-file
changes from the rehearsal itself, only the new files this record added.
