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
  weekly, and alerts on any failed job (§4, §5);
- is ready for the 14-day soak (§6).

Before you start, have: a fresh Debian 13 host with a sudo account and your
SSH key installed; a Telegram bot token and chat ID; a dead-man's-switch check
URL (the example in §1.7 uses the `hc-ping.com` format); optionally a restic repository for
offsite backups.

Known limits: the systemd units and their sandboxing were written by
inspection on a macOS machine and have not been verified on a real host
(§4). Reboots after kernel updates are not automated (§1.1).

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
sudo adduser --disabled-password --gecos "" sq
sudo usermod -aG docker sq
```

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
`config/local/secrets.json`'s `api_server.username`, `password` and
`jwt_secret_key` (32 or more random characters) plus `telegram.token` and
`chat_id` (the same Telegram bot). Generate the API credentials and the JWT key
with, for example:

```sh
openssl rand -base64 32   # jwt_secret_key
openssl rand -base64 18   # api_server password
```

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

## 3. Control

### 3.1 API: SSH tunnel only, never a public port

`compose.vps.example.yaml` (copied to `compose.vps.yaml`, ignored by git)
publishes the API on `127.0.0.1:8080` only: never on `0.0.0.0`, and never
through the firewall. Reach it from an operator machine through an SSH local
port forward:

```sh
ssh -L 8080:127.0.0.1:8080 sq@<vps-host>
# then, from the operator machine:
curl -s -u "$FREQTRADE_API_USERNAME:$FREQTRADE_API_PASSWORD" \
  http://127.0.0.1:8080/api/v1/health
```

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
curl -s -u "$FREQTRADE_API_USERNAME:$FREQTRADE_API_PASSWORD" \
  -X POST http://127.0.0.1:8080/api/v1/start
```

## 4. Automation: systemd units

Copy the unit files and enable them:

```sh
sudo cp /opt/schrodingers-quant/ops/systemd/*.service \
        /opt/schrodingers-quant/ops/systemd/*.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now schrodingers-quant.service
sudo systemctl enable --now sq-health.timer sq-backup.timer sq-retention.timer
```

| Unit | Purpose | Schedule |
| --- | --- | --- |
| `schrodingers-quant.service` | `docker compose up -d` at boot; `systemctl start/stop/status` interface | boot, on demand |
| `sq-health.timer` → `sq-health.service` | `ops/health_ping.py`: bot heartbeat and disk check, pings the dead-man's switch | every 5 minutes |
| `sq-backup.timer` → `sq-backup.service` | `ops/backup.sh`: SQLite snapshot and config bundle, optional restic push | daily, 03:15 UTC |
| `sq-retention.timer` → `sq-retention.service` | `ops/retention.sh --apply`: prune dangling Docker images | weekly |
| `sq-alert@.service` | `health_ping.py --fail "<unit> failed"`, triggered by the above via `OnFailure=` | on failure of any of the above |

Each of the four scheduled units sets `OnFailure=sq-alert@%n.service`, so a
failure of the unit itself, not just an unhealthy bot, also reaches the same
dead-man's switch. Check status and logs with:

```sh
systemctl status schrodingers-quant.service sq-health.timer sq-backup.timer sq-retention.timer
journalctl -u sq-health.service -u sq-backup.service -u sq-retention.service --since -1d
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
Without it, backups stay local under `user_data/runtime/backups/`, and only the
last 7 are kept by default.

Each backup holds every trade database in `user_data/runtime/`
(`dry-run.sqlite`, and `live.sqlite` once the live overlay has run) plus the
Jev JSONL records. When a backup holds more than one database, a restore needs
its name: `ops/restore.sh --local <backup-dir> --db live.sqlite`.

## 6. Soak

See `ops/soak-checklist.md` for the 14-day soak procedure and its drills
(container kill, reboot, network loss, restore from backup, disk-full
simulation).

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
