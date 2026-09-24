# Soak checklist: 14 days of unattended dry-run

Per ADR-0003, soak the bot unattended on the target Debian 13 host for at
least 14 days before any live authorization is considered. Per ADR-0005,
`config/base.json` selects `H1ChannelBreakout` (dry-run,
`initial_state: stopped`), so this soak also produces forward paper-trading
evidence for H1, not just infrastructure uptime. This is a procedure to
follow on the actual VPS. No VPS has been provisioned yet, so it has not
been run.

## Before starting

- [ ] Host prepared per `ops/README.md` §1
- [ ] Stack up, `docker compose ps` shows the container healthy/running
- [ ] `sq-health.timer`, `sq-backup.timer`, `sq-retention.timer` enabled and
      have fired at least once successfully (`systemctl list-timers`)
- [ ] Dead-man's-switch (HC_PING_URL) shows green in its own dashboard
- [ ] Telegram `/status` responds
- [ ] Record start time (UTC), Freqtrade image digest, git commit, and the
      config file list in use

## Daily / periodic observation (log a line per check)

- [ ] Dead-man's-switch stayed green (no silence alert)
- [ ] `docker stats --no-stream` — CPU%, memory, note if approaching the
      compose `cpus`/`mem_limit` caps
- [ ] `free -h` and `df -h /` — headroom trend, not just a point-in-time value
- [ ] `journalctl -u sq-backup.service --since -1d` — backup ran, integrity
      check ok
- [ ] No unexpected restarts: `docker compose ps` uptime, or
      `systemctl status schrodingers-quant.service`

## Drills (each at least once during the soak, spaced out, one at a time)

Record for every drill: start time, what was done, when the dead-man's-switch
or Telegram alerted (if it should have), when the system recovered on its
own vs. needed a manual step, and the total recovery time.

### D1 — Process crash

Docker treats `docker kill`/`docker stop` as a manual stop, so
`restart: unless-stopped` deliberately does not restart the container
afterwards; that is not a crash. Simulate a crash by killing the Freqtrade
process inside the container (the image has no `ps`/`pkill`):

```sh
docker compose exec -T freqtrade python -c "
import os, signal
for p in filter(str.isdigit, os.listdir('/proc')):
    if b'freqtrade trade' in open(f'/proc/{p}/cmdline', 'rb').read().replace(b'\0', b' '):
        os.kill(int(p), signal.SIGKILL)"
docker inspect -f 'restarts={{.RestartCount}}' "$(docker compose ps -aq freqtrade)"
```

Verified locally on 2026-09-24: the container restarted and `RestartCount`
became 1. Separately, check that the watchdog would notice a container someone
stopped by hand: after `docker compose stop`, the next `sq-health.service` run
must deliver a failure alert.

Expect: Docker's `restart: unless-stopped` policy brings it back without
operator action. Confirm the next `sq-health.service` run reports healthy
again within its 5-minute cadence plus one processing cycle. Record the gap
between kill and the container being observed running again, and between the
kill and the next health ping (success or failure).

### D2 — Host reboot

```sh
sudo reboot
```

Expect: `docker.service` starts, previously-running containers with
`restart: unless-stopped` come back automatically; `schrodingers-quant.service`
(enabled, `WantedBy=multi-user.target`) also runs `up -d` independently.
Confirm the bot state `stopped` is preserved as expected (config-driven, not
a reboot artifact) and, if it was previously running (`/start`ed), that an
operator deliberately decides whether to resume. Reboot does not itself
change entries-allowed state beyond what `initial_state` dictates. Record
boot-to-container-running time and boot-to-first-healthy-ping time.

### D3 — Network loss

Simulate an outbound network outage without touching SSH (so you don't lock
yourself out):

```sh
# Block outbound to the internet, but keep loopback and the current SSH
# session's established connection (ct state established,related accept
# already in the base ruleset keeps existing sessions alive):
sudo nft insert rule inet filter output oif != lo drop
# ... wait, observe ...
sudo nft flush ruleset && sudo nft -f /etc/nftables.conf   # restore
```

Expect: the dead-man's-switch alerts on silence once its grace period
elapses (health pings can no longer reach `HC_PING_URL`), even though the bot
itself may be fine locally. This is exactly the silence failure mode the
dead-man's-switch is meant to catch, distinct from an in-process notifier.
Record how long until the external alert fired, and confirm it clears after
restoring the ruleset and the next successful ping.

### D4 — Restore from backup

```sh
sudo -u sq /opt/schrodingers-quant/ops/backup.sh
# pick a recent backup dir under user_data/runtime/backups/
sudo -u sq /opt/schrodingers-quant/ops/restore.sh --local \
  /opt/schrodingers-quant/user_data/runtime/backups/<timestamp>
```

Expect: integrity checks pass (staged copy and post-restore), the previous
database is moved aside (not deleted), the container restarts, and the bot is
in the `stopped` state (see README §3.3) requiring an explicit `/start`.
Record the restore duration and confirm trade history in the restored DB
matches the source (e.g. row counts, latest trade id).

### D5 — Disk-full simulation

Use a throwaway file, not the real data:

```sh
df -h /opt   # baseline
fallocate -l 25G /opt/schrodingers-quant/.soak-disk-full-test || \
  dd if=/dev/zero of=/opt/schrodingers-quant/.soak-disk-full-test bs=1M count=25000
df -h /opt   # confirm it's now near-full, ideally >=85%
```

Expect: the next `sq-health.service` run's disk check reports unhealthy and
pings `HC_PING_URL/fail` with a disk-usage reason; confirm the alert fires.
Clean up immediately after observing it:

```sh
rm -f /opt/schrodingers-quant/.soak-disk-full-test
df -h /opt   # confirm headroom is back
```

Do not run this drill close to a scheduled `sq-backup.service` run (a backup
attempted while genuinely full could fail destructively partway); prefer
running it right after a backup completes, mid-window before the next one.

## After the soak

- [ ] All five drills recorded with timestamps and recovery times
- [ ] Resource figures (CPU/RAM/disk) stayed within the 2 vCPU / 3 GB / 30 GB
      budget across the whole soak, not just at drill points
- [ ] No un-alerted silence period longer than the dead-man's-switch's grace
      window
- [ ] Backups exist for each day, retention held at the configured count
- [ ] Write the soak record (resource figures, drill results, any
      surprises) and use it as evidence for the live-authorization decision,
      never as a substitute for the maintainer's explicit sign-off
