# Debian 13 host runbook: unattended dry-run

Target: Debian 13, 2 shared vCPU, 3 GB RAM, 30 GB SSD. This runbook covers
running the dry-run bot unattended. It does not authorize or configure live
trading. No VPS has been provisioned yet; the operator provisions the host
and follows these steps.

## 1. Host preparation

Run as a user with sudo, on a fresh Debian 13 install.

### 1.1 Base updates and unattended-upgrades

```sh
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades apt-listchanges
sudo dpkg-reconfigure -plow unattended-upgrades
```

Confirm `/etc/apt/apt.conf.d/20auto-upgrades` enables both the update and
upgrade lists (`APT::Periodic::Update-Package-Lists "1";` and
`APT::Periodic::Unattended-Upgrade "1";`). Security updates apply automatically;
a reboot after a kernel update is not automated by this runbook. Check
`/var/run/reboot-required` periodically; the soak checklist includes a reboot
drill either way.

### 1.2 Time sync

Debian 13 ships `systemd-timesyncd` enabled by default. Confirm it, and keep
the host on UTC: the container already runs with `TZ: UTC` (compose.yaml).
Matching the host avoids off-by-hours confusion when correlating host and
container logs, and when reading systemd timer `OnCalendar=` times below.

```sh
timedatectl set-timezone UTC
timedatectl status   # NTP synchronized: yes
```

### 1.3 SSH: key-only, no root login

Before disabling password auth, confirm your public key is already in
`~/.ssh/authorized_keys` for the account you will use, and that you can
open a *second* session successfully before closing the first.

In `/etc/ssh/sshd_config` (or a drop-in under `/etc/ssh/sshd_config.d/`):

```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
```

```sh
sudo systemctl reload ssh
```

### 1.4 Firewall: default deny inbound except SSH

Using `nftables` (Debian 13 default):

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

Note there is deliberately no rule opening 8080: the Freqtrade API is never
published beyond `127.0.0.1` (see §3), so no inbound firewall rule is needed
for it. The port never reaches the network interface nftables filters.
If your provider also offers a security-group/cloud firewall, mirror this
(deny all inbound except SSH) there too, for defense in depth.

Consider changing the SSH port and/or adding fail2ban if the host sees
credential-stuffing traffic; not required for the soak.

### 1.5 Docker

Install Docker Engine + the Compose plugin from Docker's official apt
repository (Debian 13's own repo may lag on the Compose plugin version this
project's `compose.yaml` syntax assumes):

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
sudo. Operators log in as their own account and `sudo -iu sq` or run
`docker compose` commands as `sq` explicitly when needed.

Membership in the `docker` group is root-equivalent on the host: a process
that can talk to the Docker socket can bind-mount `/` into a container and
read or write anything root can. Adding `sq` to `docker` (as this step does)
is a deliberate trade-off, not a sandboxed, unprivileged account. Treat
compromise of the `sq` user the same as compromise of root.

### 1.7 Clone and place secrets

```sh
sudo mkdir -p /opt/schrodingers-quant
sudo chown sq:sq /opt/schrodingers-quant
sudo -iu sq git clone https://github.com/sebastianspicker/schrodingers-quant.git /opt/schrodingers-quant
```

Create the two gitignored local config files, copied from the tracked
examples and filled in (never commit these):

```sh
sudo -iu sq cp /opt/schrodingers-quant/config/examples/vps-dryrun.example.json \
  /opt/schrodingers-quant/config/local/vps.json
sudo -iu sq cp /opt/schrodingers-quant/config/examples/secrets.example.json \
  /opt/schrodingers-quant/config/local/secrets.json
sudo -u sq chmod 600 /opt/schrodingers-quant/config/local/vps.json \
  /opt/schrodingers-quant/config/local/secrets.json
```

Fill in `config/local/vps.json`'s `telegram.token`/`telegram.chat_id`, and
`config/local/secrets.json`'s `api_server.username`/`password`/
`jwt_secret_key` (32+ random characters) and `telegram.token`/`chat_id`
(same bot). Generate the API credentials and JWT key with, e.g.:

```sh
openssl rand -base64 32   # jwt_secret_key
openssl rand -base64 18   # api_server password
```

Create `/etc/schrodingers-quant/ops.env` (mode 600, owned by `sq`) for the
host-side scripts in `ops/`. See §4 below for its contents (HC_PING_URL,
FREQTRADE_API_USERNAME/PASSWORD, optional RESTIC_*).
This file is separate from `config/local/secrets.json`: the former is read
by systemd (`EnvironmentFile=`) for the host scripts, the latter is layered
into the Freqtrade container's config.

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

Copy the VPS compose overlay and point Compose at it by default, so every
plain `docker compose` command (the systemd units in §4, `ops/backup.sh`,
`ops/restore.sh`) picks it up without needing a `-f` flag on each invocation:

```sh
sudo -u sq cp /opt/schrodingers-quant/compose.vps.example.yaml \
  /opt/schrodingers-quant/compose.vps.yaml
sudo -u sq sh -c 'echo "COMPOSE_FILE=compose.yaml:compose.vps.yaml" \
  >> /opt/schrodingers-quant/.env'
```

`.env` is gitignored (never tracked); Compose reads it and `compose.vps.yaml`
relative to the current working directory, so always run `docker compose`
from `/opt/schrodingers-quant` (the systemd units set `WorkingDirectory=`
there; `ops/backup.sh`/`ops/restore.sh` `cd` there internally).

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
entries and no trading, until an operator explicitly starts it (§3.3). This
is true after every container (re)start, including after a restore (see
`ops/restore.sh`).

## 3. Control

### 3.1 API: SSH tunnel only, never a public port

`compose.vps.example.yaml` (copy to `compose.vps.yaml`, gitignored) publishes
the API on `127.0.0.1:8080` only, never `0.0.0.0` and never through the
firewall. Reach it from an operator machine with an SSH local port forward:

```sh
ssh -L 8080:127.0.0.1:8080 sq@<vps-host>
# then, from the operator machine:
curl -s -u "$FREQTRADE_API_USERNAME:$FREQTRADE_API_PASSWORD" \
  http://127.0.0.1:8080/api/v1/health
```

### 3.2 Telegram

`config/local/vps.json` enables Telegram with commands including:

- `/status` — open trades
- `/stopentry` — block new entries; existing positions still manage exits
  (protective exits are never gated by this)
- `/start` — resume entries after `/stopentry` or after a restore
- `/stop` — stop the bot entirely (also blocks exit processing; use
  `/stopentry` instead unless you mean to halt everything)
- `/forceexit <trade_id|all>` — close a position immediately

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
| `sq-health.timer` → `sq-health.service` | `ops/health_ping.py`: bot heartbeat + disk check, pings the dead-man's-switch | every 5 minutes |
| `sq-backup.timer` → `sq-backup.service` | `ops/backup.sh`: sqlite snapshot + config bundle, optional restic push | daily, 03:15 UTC |
| `sq-retention.timer` → `sq-retention.service` | `ops/retention.sh --apply`: prune dangling Docker images | weekly |
| `sq-alert@.service` | `health_ping.py --fail "<unit> failed"`, triggered by the above via `OnFailure=` | on failure of any of the above |

Each of the four scheduled units sets `OnFailure=sq-alert@%n.service`, so a
failure of the unit itself (not just an unhealthy bot) also reaches the same
dead-man's-switch. Check status and logs with:

```sh
systemctl status schrodingers-quant.service sq-health.timer sq-backup.timer sq-retention.timer
journalctl -u sq-health.service -u sq-backup.service -u sq-retention.service --since -1d
```

`systemd-analyze verify` is unavailable outside Linux; it was not run for
these units on this macOS development machine. Run it on the actual host
before relying on the units:

```sh
sudo systemd-analyze verify /etc/systemd/system/sq-health.service   # etc.
```

Each unit's `[Service]` section also sets cheap sandboxing directives
(`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`,
`ProtectKernelTunables`, `ProtectControlGroups`, `RestrictSUIDSGID`);
`ProtectSystem=full` (not `strict`) and no `ProtectHome=` because
`schrodingers-quant.service` needs normal docker CLI access and
`sq-backup.service` needs restic's cache under the `sq` user's home. These
were written by inspection, not verified with `systemd-analyze` on this
macOS machine. Also check `systemd-analyze security <unit>` on the actual
host alongside `verify` above.

## 5. Backup, restore, retention

See `ops/backup.sh`, `ops/restore.sh`, `ops/retention.sh` (each has a header
comment with usage and configuration). `ops/local-rehearsal.md`
records a local Docker rehearsal of the full cycle. Restic is optional
(`RESTIC_REPOSITORY` in `/etc/schrodingers-quant/ops.env`); without it,
backups stay local under `user_data/runtime/backups/`, bounded to the last 7
by default.

Each backup holds every trade DB in `user_data/runtime/` (`dry-run.sqlite`,
and `live.sqlite` once the live overlay has run) plus the Jev JSONL records.
When a backup holds more than one DB, restore needs the name:
`ops/restore.sh --local <backup-dir> --db live.sqlite`.

## 6. Soak

See `ops/soak-checklist.md` for the 14-day soak procedure and drills
(container kill, reboot, network loss, restore-from-backup, disk-full
simulation).
