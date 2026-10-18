# Debian 13 host runbook: unattended dry-run

Target: Debian 13, 2 shared vCPU, 3 GB RAM, 30 GB SSD. This runbook covers
running the dry-run bot unattended. It does not authorize or configure live
trading. No VPS has been provisioned yet; the operator provisions the host and
follows these steps.

## In short

By the end of this runbook the host:

- is patched automatically, on UTC, reachable only by SSH key, with every other
  inbound port closed (§1);
- runs the bot in Docker as a dedicated `sq` user, in dry-run and in the
  `stopped` state, so it trades nothing until you send `/start` (§2, §3);
- is controlled only through Telegram or the API over an SSH tunnel; the API
  is never exposed to the network (§3);
- pings a dead-man's switch every 5 minutes, backs up daily, prunes old images
  weekly, writes a daily forward-test report, and alerts on any failed job
  (§4, §5);
- is ready for the 14-day soak (§6).

Before you start, have: a fresh Debian 13 host with a sudo account and your
SSH key installed; a Telegram bot token and chat ID; a dead-man's-switch check
URL (the example in §1.7 uses the `hc-ping.com` format); optionally a restic repository for
offsite backups.

Known limits: the systemd units and their sandboxing were written by
inspection on a macOS machine and have not been verified on a real host
(§4). Reboots after kernel updates are not automated (§1.1).

The bot is capped at 2 GB RAM and 1.5 CPU cores. Read-only tools, including the
daily forward report, are capped at 512 MB RAM and one CPU core, with one
NumPy/BLAS worker thread. These are ceilings, not reserved memory: leave room
for Debian, Docker and backups, observe peak memory during the soak, and keep
the optional Jev worker disabled unless measured headroom permits it. Research
backtests have a separate 2 GB ceiling; run them on a development machine,
outside the VPS trading stack.

## 1. Host preparation

Run as a user with sudo, on a fresh Debian 13 install.

### 1.1 Base updates and unattended-upgrades

```sh
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades apt-listchanges
sudo dpkg-reconfigure -plow unattended-upgrades
```

Confirm that `/etc/apt/apt.conf.d/20auto-upgrades` enables both the update and
the upgrade lists (`APT::Periodic::Update-Package-Lists "1";` and
`APT::Periodic::Unattended-Upgrade "1";`). Security updates then apply
automatically. A reboot after a kernel update is not automated by this
runbook: check `/var/run/reboot-required` periodically. The soak checklist
includes a reboot drill either way.

A minimal image lacks tools that the checkout and the host scripts use. Install
them now (`restic` is only needed for the optional offsite backup in §5):

```sh
sudo apt install -y git sqlite3 python3 openssl restic
```

### 1.2 Time sync

Debian 13 ships `systemd-timesyncd` enabled by default. Confirm it, and keep
the host on UTC: the container already runs with `TZ: UTC` (`compose.yaml`).
Matching the host avoids off-by-hours confusion when you correlate host and
container logs, and when you read the systemd timer `OnCalendar=` times below.

```sh
timedatectl set-timezone UTC
timedatectl status   # NTP synchronized: yes
```

### 1.3 SSH: key-only, no root login

Before you disable password login, confirm that your public key is already in
`~/.ssh/authorized_keys` for the account you will use, and that you can open a
*second* session successfully before closing the first. Otherwise you can lock
yourself out.

In `/etc/ssh/sshd_config` (or a drop-in under `/etc/ssh/sshd_config.d/`):

```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
```

```sh
sudo systemctl reload ssh
```

### 1.4 Firewall: deny all inbound traffic except SSH

Using `nftables` (the Debian 13 default):

```sh
sudo apt install -y nftables
sudo tee /etc/nftables.conf >/dev/null <<'EOF'
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    ct state established,related accept
    iif lo accept
    tcp dport 22 accept
    ip protocol icmp accept
    ip6 nexthdr icmpv6 accept
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
  }
  chain output {
    type filter hook output priority 0; policy accept;
  }
}
EOF
sudo systemctl enable --now nftables
```

There is deliberately no rule opening port 8080. The Freqtrade API is never
published beyond `127.0.0.1` (see §3), so it needs no inbound firewall rule:
the port never reaches the network interface that nftables filters. If your
provider also offers a cloud firewall or security group, mirror this there too
(deny all inbound except SSH), for defense in depth.

Consider changing the SSH port or adding fail2ban if the host sees
credential-stuffing traffic (automated password-guessing). Neither is required
for the soak.

### 1.5 Docker

Install Docker Engine and the Compose plugin from Docker's official apt
repository. Debian 13's own repository may lag behind the Compose plugin
version that this project's `compose.yaml` syntax assumes.

```sh
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

### 1.6 Dedicated `sq` user

```sh
getent passwd 1000 && echo "uid 1000 taken: use user: in compose.vps.yaml" || true
sudo adduser --uid 1000 --disabled-password --gecos "" sq
sudo usermod -aG docker sq
```

`sq` gets uid 1000 on purpose: the container's `ftuser` is uid 1000, and both
write to the bind-mounted `user_data/runtime` (the bot creates
`dry-run.sqlite`, the host scripts write backups and reports). If `getent`
prints a line, uid 1000 already belongs to another account (often the
provider's default user). Create `sq` without `--uid`, check `id sq`, and set
`user: "<uid>:<gid>"` for the `freqtrade` service in `compose.vps.yaml` so the
container runs as `sq`.

`sq` runs the systemd units in §4 and owns the checkout. It does not need
sudo. Operators log in with their own account and use `sudo -iu sq`, or run
`docker compose` commands as `sq` explicitly when needed.

Membership in the `docker` group is equivalent to root on the host: a process
that can talk to the Docker socket can bind-mount `/` into a container and
read or write anything root can. Adding `sq` to `docker`, as this step does,
is a deliberate trade-off, not a sandboxed, unprivileged account. Treat a
compromise of the `sq` user the same as a compromise of root.

### 1.7 Clone and place secrets

```sh
sudo mkdir -p /opt/schrodingers-quant
sudo chown sq:sq /opt/schrodingers-quant
sudo -iu sq git clone https://github.com/sebastianspicker/schrodingers-quant.git /opt/schrodingers-quant
```

Create the two local config files, copied from the tracked examples and then
filled in. Both are ignored by git; never commit them.

```sh
sudo -iu sq cp /opt/schrodingers-quant/config/examples/vps-dryrun.example.json \
  /opt/schrodingers-quant/config/local/vps.json
sudo -iu sq cp /opt/schrodingers-quant/config/examples/secrets.example.json \
  /opt/schrodingers-quant/config/local/secrets.json
sudo -u sq chmod 600 /opt/schrodingers-quant/config/local/vps.json \
  /opt/schrodingers-quant/config/local/secrets.json
```

Fill in `config/local/vps.json`'s `telegram.token` and `telegram.chat_id`, and
`config/local/secrets.json`'s `api_server.username`, `password`,
`jwt_secret_key` (32 or more random characters) and `ws_token` plus
`telegram.token` and `chat_id` (the same Telegram bot). Generate the API
credentials and the JWT key with, for example:

```sh
openssl rand -base64 32   # jwt_secret_key
openssl rand -base64 18   # api_server password
openssl rand -base64 24   # api_server ws_token
```

`make validate-local` then checks base → `vps.json` → `secrets.json` and fails
on any empty or placeholder credential.

Create `/etc/schrodingers-quant/ops.env` (mode 600, owned by `sq`) for the
host-side scripts in `ops/`. See §4 for how it is used; it holds
`HC_PING_URL`, `FREQTRADE_API_USERNAME` and `FREQTRADE_API_PASSWORD`, and
optionally `RESTIC_*`. This file is separate from `config/local/secrets.json`:
systemd reads `ops.env` (`EnvironmentFile=`) for the host scripts, while
`secrets.json` is layered into the Freqtrade container's config.

```sh
sudo install -d -m 700 -o sq -g sq /etc/schrodingers-quant
sudo -u sq tee /etc/schrodingers-quant/ops.env >/dev/null <<'EOF'
HC_PING_URL=https://hc-ping.com/<your-check-uuid>
FREQTRADE_API_USERNAME=<same as config/local/secrets.json api_server.username>
FREQTRADE_API_PASSWORD=<same as config/local/secrets.json api_server.password>
# Optional, only if backups should also push offsite:
#RESTIC_REPOSITORY=...
#RESTIC_PASSWORD=...
EOF
sudo chmod 600 /etc/schrodingers-quant/ops.env
```

## 2. Bring the stack up

Copy the VPS compose overlay and make it the default, so that every plain
`docker compose` command (the systemd units in §4, `ops/backup.sh`,
`ops/restore.sh`) picks it up without a `-f` flag on each call:

```sh
sudo -u sq cp /opt/schrodingers-quant/compose.vps.example.yaml \
  /opt/schrodingers-quant/compose.vps.yaml
sudo -u sq sh -c 'echo "COMPOSE_FILE=compose.yaml:compose.vps.yaml" \
  >> /opt/schrodingers-quant/.env'
```

`.env` is ignored by git (never tracked). Compose reads it, and
`compose.vps.yaml`, relative to the current working directory, so always run
`docker compose` from `/opt/schrodingers-quant`. The systemd units set
`WorkingDirectory=` there, and `ops/backup.sh` and `ops/restore.sh` `cd` there
internally.

```sh
cd /opt/schrodingers-quant
sudo -u sq docker compose config --quiet   # sanity check: merges base + vps
```

Pull the images once before the first start. The systemd service gives
`docker compose up -d` only 120 seconds (`TimeoutStartSec`), which a first
image pull on a small VPS can exceed:

```sh
sudo -u sq docker compose pull
```

Then install and enable the systemd units (§4), which run
`docker compose up -d` for you. To start manually first, for verification:

```sh
sudo -u sq docker compose up -d
sudo -u sq docker compose ps
```

The container starts with the bot in the `stopped` state
(`config/base.json`'s `initial_state: stopped`). It stays stopped, with no
entries and no trading, until an operator explicitly starts it (§3.3). This is
true after every container start or restart, including after a restore (see
`ops/restore.sh`).

Daily forward reports now maintain `runtime/market-data.sqlite` without a
research container. Backups snapshot it with the trade databases and copy
reports, the pinned window manifest and the STOP latch. Restore matching
evidence alongside a trade database with `ops/restore.sh ... --with-evidence`
(replaced files are kept as `.pre-restore.<timestamp>`); database restore alone
is incomplete.
See [forward protocol v2](../docs/forward-test.md) for error recovery and
explicit account capital. `make validate-report` exercises the real pinned
Freqtrade database schema and H2 callback using temporary synthetic data.

## 3. Control

### 3.1 API: SSH tunnel only, never a public port

`compose.vps.example.yaml` (copied to `compose.vps.yaml`, ignored by git)
publishes the API on `127.0.0.1:8080` only: never on `0.0.0.0`, and never
through the firewall. Reach it from an operator machine through an SSH local
port forward:

```sh
ssh -L 8080:127.0.0.1:8080 sq@<vps-host>
# then, from the operator machine:
curl -s -u "$API_USER:$API_PASS" http://127.0.0.1:8080/api/v1/health
```

`API_USER` and `API_PASS` are placeholders for your shell on the operator
machine, not variables that exist there. Their values are `api_server.username`
and `api_server.password` in `config/local/secrets.json` on the VPS (the same
values as `FREQTRADE_API_USERNAME` and `FREQTRADE_API_PASSWORD` in
`/etc/schrodingers-quant/ops.env`). Export them in your own shell first, or
type them in place of the variables.

### 3.2 Telegram

`config/local/vps.json` enables Telegram, with commands including:

- `/status`: show open trades.
- `/stopentry`: block new entries. Existing positions still manage their exits;
  protective exits are never gated by this.
- `/start`: resume entries after `/stopentry` or after a restore.
- `/stop`: stop the bot entirely. This also blocks exit processing, so use
  `/stopentry` instead unless you mean to halt everything.
- `/forceexit <trade_id|all>`: close a position immediately.

### 3.3 Resuming after a restore or a deliberate stop

`initial_state: stopped` means every container start comes back not trading.
After verifying a restore or a restart, resume deliberately:

```sh
# Telegram
/start
# or, over the SSH tunnel from §3.1
curl -s -u "$API_USER:$API_PASS" \
  -X POST http://127.0.0.1:8080/api/v1/start
```

## 4. Automation: systemd units

If `RESTIC_REPOSITORY` is set in `ops.env`, initialize the repository once
before enabling `sq-backup.timer`, with the same environment the backup uses;
otherwise every offsite push fails. Skip this when backups stay local.

```sh
sudo -u sq sh -c 'set -a; . /etc/schrodingers-quant/ops.env; set +a; restic init'
```

Copy the unit files and enable them:

```sh
sudo cp /opt/schrodingers-quant/ops/systemd/*.service \
        /opt/schrodingers-quant/ops/systemd/*.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now schrodingers-quant.service
sudo systemctl enable --now sq-health.timer sq-backup.timer sq-retention.timer \
  sq-forward-report.timer
```

| Unit | Purpose | Schedule |
| --- | --- | --- |
| `schrodingers-quant.service` | `docker compose up -d` at boot; `systemctl start/stop/status` interface | boot, on demand |
| `sq-health.timer` → `sq-health.service` | `ops/health_ping.py`: bot heartbeat and disk check, pings the dead-man's switch | every 5 minutes |
| `sq-backup.timer` → `sq-backup.service` | `ops/backup.sh`: SQLite snapshot and config bundle, optional restic push | daily, 03:15 UTC |
| `sq-retention.timer` → `sq-retention.service` | `ops/retention.sh --apply`: prune dangling Docker images and superseded `freqtradeorg/freqtrade` images (never the digest pinned in `compose.yaml`) | weekly |
| `sq-forward-report.timer` → `sq-forward-report.service` | `ops/forward_report.sh`: forward paper-test report (`sq.live.forward`) | daily, 04:15 UTC |
| `sq-alert@.service` | `health_ping.py --fail "<unit> failed"`, triggered by the above via `OnFailure=` | on failure of any of the above |

`sq-forward-report.service` runs the read-only forward report in the tools
container (see [forward test](../docs/forward-test.md)) after the backup and
writes `user_data/runtime/reports/forward-<UTC timestamp>.json`. It needs no
credentials. Exit code 4 means a criterion (F1, F2 or F3) failed, STOP: the
unit fails, `sq-alert@` runs, and the dead-man's switch is told. Exit code 1 is
an error and alerts the same way. Like the other units, it has not been run on
a real host.

Once the daily report is configured, add these settings to
`/etc/schrodingers-quant/ops.env`:

```sh
HEALTH_FORWARD_REPORT=/opt/schrodingers-quant/user_data/runtime/reports/forward-latest.json
HEALTH_FORWARD_MAX_AGE_SECONDS=129600
```

The five-minute health check then requires a CONTINUE report generated within
36 hours. STOP, a missing or malformed report, an error record, a future
generation time, and a stale report all keep the monitor failed even when the
bot heartbeat is fresh. This avoids the next healthy heartbeat clearing a
forward-test STOP. The reporter also keeps the first STOP in a sibling
`forward-stop.json` latch: while that file exists, even a fresh CONTINUE report
cannot clear the failure. Investigate and record the incident, archive that
latch, and require a fresh CONTINUE report before restarting entries. This check is
disabled when `HEALTH_FORWARD_REPORT` is unset, so initial host setup can run
before the first report exists. The check uses `generated_at` inside the JSON,
so copying an old report does not refresh it. Other failed jobs still need
their own persistent monitor checks if they must stay failed independently of
the bot heartbeat.

Two settings for it, both in `/etc/schrodingers-quant/ops.env`, which the unit
reads if present:

- `SQ_FORWARD_ARGS`: extra arguments for the report, word-split by systemd.
  Set the forward window's start and initial account capital when the soak
  begins, for example `SQ_FORWARD_ARGS=--since 2026-10-15 --initial-capital 10`.
  Public candles are archived automatically in runtime/market-data.sqlite;
  a manual candle file is only needed for verified historical imports/repair.
  Without `--since`, the diagnostic window starts at the first order, so earlier
  missed entries cannot be tested. Use an explicit start for the soak.
- The report is written by the container's user (`ftuser`, uid 1000), which is
  also `sq` when §1.6 was followed, so `sq` already owns the directory. Create
  it once before the first run:
  `cd /opt/schrodingers-quant && sudo -u sq mkdir -p user_data/runtime/reports user_data/runtime/jev`.
  A `PermissionError` in the journal on the first run means this step was
  skipped, or `sq` is not uid 1000 and the container does not run as `sq`
  (see §1.6).

Each of the scheduled units sets `OnFailure=sq-alert@%n.service`, so a
failure of the unit itself, not just an unhealthy bot, also reaches the same
dead-man's switch. Check status and logs with:

```sh
systemctl status schrodingers-quant.service sq-health.timer sq-backup.timer sq-retention.timer sq-forward-report.timer
journalctl -u sq-health.service -u sq-backup.service -u sq-retention.service -u sq-forward-report.service --since -1d
```

Not yet verified: `systemd-analyze verify` is unavailable outside Linux, so it
was not run for these units on the macOS development machine. Run it on the
actual host before relying on the units:

```sh
sudo systemd-analyze verify /etc/systemd/system/sq-health.service   # etc.
```

Each unit's `[Service]` section also sets cheap sandboxing directives
(`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`,
`ProtectKernelTunables`, `ProtectControlGroups`, `RestrictSUIDSGID`). It uses
`ProtectSystem=full` rather than `strict`, and no `ProtectHome=`, because
`schrodingers-quant.service` needs normal Docker CLI access and
`sq-backup.service` needs restic's cache under the `sq` user's home. These
directives were written by inspection, not verified with `systemd-analyze` on
the macOS machine. On the actual host, also check
`systemd-analyze security <unit>` alongside `verify` above.

## 5. Backup, restore, retention

See `ops/backup.sh`, `ops/restore.sh` and `ops/retention.sh`; each has a
header comment with usage and configuration. `ops/local-rehearsal.md` records a
local Docker rehearsal of the full cycle.

Restic is optional (`RESTIC_REPOSITORY` in `/etc/schrodingers-quant/ops.env`).
When set, each push is tagged `sq-backup` and the retention `restic forget`
only ever considers snapshots with that tag, grouped by host and tags; older
untagged snapshots already in the repository are kept and must be pruned by
hand. Without restic, backups stay local under `user_data/runtime/backups/`,
and only the last 7 are kept by default.

Each backup holds every `*.sqlite` in `user_data/runtime/` (`dry-run.sqlite`,
`market-data.sqlite` once the forward report has run, and `live.sqlite` once
the live overlay has run) plus the Jev JSONL records. A backup directory
therefore usually holds more than one database, and a restore needs the trade
database's name: `ops/restore.sh --local <backup-dir> --db dry-run.sqlite`
(`--db live.sqlite` in live mode). Without `--db` the restore refuses and lists
the databases it found.

## 6. Soak

See `ops/soak-checklist.md` for the 14-day soak procedure and its drills
(container kill, reboot, network loss, restore from backup, disk-full
simulation). During the soak, the daily forward report
(`journalctl -u sq-forward-report.service --since -1d`) prints one summary
line per day (window, closed trades, net return, drawdown, verdict, whether an
incident is latched) for the soak log. Its verdict must stay CONTINUE.

## Glossary

| Term | Meaning here |
| --- | --- |
| Dry-run | Freqtrade's simulation mode: no orders are sent. |
| `stopped` state | The bot runs but opens no trades until an operator sends `/start`. |
| Unattended-upgrades | Debian's package for installing security updates automatically. |
| nftables | Debian's built-in firewall. |
| SSH local port forward (tunnel) | `ssh -L` makes a port on the server reachable on your own machine through the encrypted SSH connection. |
| Docker socket | The control channel of the Docker daemon; access to it amounts to root access. |
| Compose overlay | An extra Compose file (`compose.vps.yaml`) merged over `compose.yaml`. |
| JWT secret key | The secret the Freqtrade API uses to sign login tokens. |
| systemd unit / timer | A service definition, and a schedule that starts a service. |
| `OnFailure=` | A systemd setting that starts another unit (here the alert) when a unit fails. |
| Dead-man's switch | An external monitor (`HC_PING_URL`) that alerts when the regular ping stops. |
| Restic | A backup tool that can push encrypted backups to offsite storage. |
| Dangling image | An old, untagged Docker image layer that nothing uses. |
| Soak | The 14-day unattended dry-run with drills that must pass before a live pilot. |
