# Security policy

## Report a vulnerability

Don't open a public issue, discussion or pull request for a suspected
vulnerability. Use the repository's Security tab to open a private
vulnerability report. If that isn't available, contact the repository owner
privately before sending details.

Say how to reproduce the issue, which commit is affected, and what the impact
could be. Remove API keys, Telegram tokens, account exports, trade databases,
hostnames and IP addresses first.

Fixes are made against the default branch. There are no maintained releases.

## What counts

This project holds exchange credentials on an operator's host and can place
real orders once the live overlay is applied. Reports that matter most:

- anything that could leak credentials from `config/local/`, `.env` or
  `/etc/schrodingers-quant/ops.env`, including through logs or error messages;
- a path by which project code, the Jev worker or a strategy could place,
  cancel or edit an order outside Freqtrade;
- a way for the tracked defaults to start trading live without the explicit
  live overlay, credentials and `/start`;
- exposure of the Freqtrade API beyond `127.0.0.1`.

## Operator responsibilities

- Use an exchange API key with query and trade permissions only. Never enable
  withdrawals. Restrict it to the server's IP address if the exchange allows it.
- Keep secrets in the git-ignored files named above, with mode 600.
- Reach the API through an SSH tunnel; don't publish port 8080.
- Membership in the `docker` group is root-equivalent. Treat the `sq` service
  user accordingly (see the [host runbook](ops/README.md)).
