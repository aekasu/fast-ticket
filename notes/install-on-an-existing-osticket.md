# Installing fast-ticket against an existing osTicket

fast-ticket needs two things: a **bridge plugin** inside osTicket (it executes writes and answers login)
and the **API** (FastAPI) that can reach osTicket's database. Nothing else in osTicket is modified —
no core file is patched, no osTicket table is written to.

## Requirements

| | |
|---|---|
| osTicket | 1.18.x (the version the queries and handlers were verified against; `make doctor` warns on others) |
| PHP (osTicket host) | 8.0+ with the CLI. The `pcntl` extension is optional: with it the worker answers in about a second, without it commands run at cron speed (about once a minute) |
| Database | MySQL 5.6+ / MariaDB 10+, reachable from the API host |

## 1. Install the plugin

```bash
make bridge-zip                                   # → dist/fastapi_integration-<version>.zip
unzip dist/fastapi_integration-*.zip -d /path/to/osticket/include/plugins/
```

In osTicket: **Admin Panel → Manage → Plugins → Add New Plugin → FastAPI Integration Bridge → Install**,
then open it, **Add New Instance**, set it **Enabled**, and save the settings:

* **Integration key** — leave blank and save to generate one (or paste a random string of 32+ characters).
  Save again and copy it: it is the API's `FASTAPI_INTEGRATION_KEY`.

Enabling the plugin creates its two tables (`<prefix>integration_inbox`, `<prefix>integration_outbox`)
using osTicket's own table prefix. The osTicket database user therefore needs `CREATE` once; if it cannot
have it, run the statements in `bridge/schema.php` by hand (replace `{inbox}` / `{outbox}`).

Headless alternative (what the Docker image does): `php include/plugins/fastapi_integration/install.php --key=<key>`.

## 2. Run something that executes commands

Commands queued by the API wait in the inbox until the plugin runs them. Pick either (both is fine — they
share an atomic claim):

* **Worker (recommended, ~1 s):** keep `php include/plugins/fastapi_integration/worker.php` running under a
  supervisor, as the web server's user. systemd:

  ```ini
  [Service]
  User=www-data
  WorkingDirectory=/path/to/osticket
  ExecStart=/usr/bin/php include/plugins/fastapi_integration/worker.php
  Environment=FASTAPI_INTEGRATION_KEY=<only if you did not save it in the plugin settings>
  Restart=always
  ```
* **osTicket's cron (fallback, ~60 s):** the standard `* * * * * php /path/to/osticket/api/cron.php` entry
  already drives the plugin.

## 3. Configure the API

```bash
DATABASE_URL=mysql+aiomysql://fast_ticket:<pw>@<db-host>:3306/<osticket-db>
OSTICKET_TABLE_PREFIX=ost_                 # the prefix osTicket was installed with
FASTAPI_INTEGRATION_KEY=<the key from step 1>
OSTICKET_AUTH_URL=https://<osticket-host>/api/auth/integration    # use HTTPS off localhost
JWT_SECRET_KEY=<openssl rand -hex 48>       # 32+ characters; set REQUIRE_JWT_SECRET=true to refuse to boot without it
TRUSTED_PROXY_COUNT=0                       # set to N only if N reverse proxies append to X-Forwarded-For
```

Agents whose accounts use two-factor authentication (or whom osTicket requires to enrol) are refused at login with a
clear message — the API cannot complete a second factor yet. Use a non-2FA agent account for API access.

Give the API a **least-privilege database user** — it reads osTicket's tables and only writes its own queue:

```sql
GRANT SELECT ON `<osticket-db>`.* TO 'fast_ticket'@'%';
GRANT INSERT, DELETE ON `<osticket-db>`.`<prefix>integration_inbox`  TO 'fast_ticket'@'%';
GRANT UPDATE, DELETE ON `<osticket-db>`.`<prefix>integration_outbox` TO 'fast_ticket'@'%';
```

## 4. Check it

```bash
make doctor        # or: python -m app.doctor
```

It verifies the database and prefix, the plugin's tables and columns, **every read query against your real
schema** (naming any that fails), the plugin handshake (osTicket and plugin versions, matching prefix), the
database user's privileges (printing the grants above if they are too broad) and stuck commands. A failure
exits 1. Fix what it names, run it again.

## Troubleshooting

| `make doctor` says | Meaning |
|---|---|
| `no <prefix>config table` | wrong `OSTICKET_TABLE_PREFIX` or database |
| `table … missing` | the plugin is not enabled yet, or its database user lacked `CREATE` |
| `handshake … 403` | the API's key differs from the plugin's |
| `handshake … HTTP 404/500` | plugin not enabled, or `OSTICKET_AUTH_URL` wrong |
| `read queries … failed` | this osTicket's schema differs from 1.18 — the failing function is named |
| `command(s) pending for over 2 minutes` | neither the worker nor cron is running |
