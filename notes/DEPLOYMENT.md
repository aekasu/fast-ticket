# Deploying fast-ticket

What differs between the laptop stack (`make start`) and one you would leave running. The browser-facing side
(same-origin proxy, CORS) is in `CLIENTS.md`; the threat model is in `SECURITY.md`.

## The two ways to run the compose file

| | Command | What you get |
|---|---|---|
| Development | `make start` (`docker compose up`) | `docker-compose.yml` **plus** `docker-compose.override.yml`: the code is read from the working tree and the API reloads on change |
| Production | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` | Naming the files stops the override from loading. No reload, no mounted source, the API as the least-privilege database user |

## What the production files do

- **The API container is unprivileged.** It runs as uid 10001, with a read-only root filesystem, a `tmpfs` `/tmp`, no Linux
  capabilities and `no-new-privileges`. The image is multi-stage (`docker/fastapi/Dockerfile`): the runtime stage has
  neither the dev dependencies nor the tests, and uv is pinned.
- **The API does not connect as the osTicket user.** `docker-compose.prod.yml` adds a one-shot `db-grants` service that
  creates `fast_ticket` (password `FASTAPI_DB_PASSWORD`) with `SELECT` on osTicket's database and
  `INSERT, DELETE` / `UPDATE, DELETE` on the two integration queue tables, nothing else. Every write to osTicket's own
  tables therefore has to go through the bridge, as designed, and a stolen API credential cannot edit tickets directly.
  `make doctor` shows `database user privileges — read-only on osTicket tables`.
  osTicket itself, the inbox worker and cron still use the `osticket` user: they run osTicket.
- **Ports are bound to `127.0.0.1`** (`API_BIND`, `OSTICKET_BIND`; the database's host port always is). Nothing is
  reachable from the network until a proxy publishes it.
- **No default admin password** (`OSTICKET_ADMIN_PASSWORD` is required) and **`/docs` is off** (`API_DOCS_ENABLED=false`;
  turn it on in a dev instance when a frontend needs the schema).

## Secrets (`.env`, never committed)

`make init` generates them: `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `FASTAPI_DB_PASSWORD`, `FASTAPI_INTEGRATION_KEY`,
`JWT_SECRET_KEY`. Set `OSTICKET_ADMIN_PASSWORD` yourself. Rotate by editing `.env` and re-running the `up` command
(`db-grants` re-applies the API user's password; changing `FASTAPI_INTEGRATION_KEY` needs the plugin's setting changed
too — Admin Panel → Manage → Plugins → fast-ticket). Changing `JWT_SECRET_KEY` signs every agent out.

## TLS and the proxy

The API speaks plain HTTP. Put a TLS-terminating proxy in front and publish only that. A minimal Caddyfile for one host:

```
helpdesk.example.com {
    encode zstd gzip
    handle /api/* {
        reverse_proxy 127.0.0.1:8000     # WebSocket upgrades (/api/v1/ws) pass through
    }
    handle {
        root * /srv/frontend             # the client's static files
        try_files {path} /index.html
        file_server
    }
}
```

Then in `.env`: `TRUSTED_PROXY_COUNT=1` (the client address behind rate limits and the login lockout is read from
`X-Forwarded-For` only as far as you say), and `CORS_ORIGINS=` empty for the same-origin shape above. Keep osTicket's own
Staff Control Panel off the public internet unless you need it (it is on `OSTICKET_BIND:OSTICKET_PORT`).

More than one API worker or replica: point `RATE_LIMIT_STORAGE_URI` at Redis, otherwise each process counts on its own
(`KNOWN_LIMITS.md`).

## Backups

- The database (`mysqldump` / `mariadb-dump` of `osticket`): osTicket's data and the two queue tables.
- The `osticket_config` volume: it holds `ost-config.php`, whose secret salt osTicket needs to read what it encrypted.
  A restore without it is a different installation.
- Uploaded files are in the database unless a storage plugin moved them; the API reads them either way.

## Upgrading

1. `git pull`, then rebuild: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
   The plugin (`bridge/`) is baked into the osTicket image; the worker and cron containers restart with it.
2. `docker compose exec api python -m app.doctor` (or `make doctor`): prefix, plugin version handshake, every read
   query against the live schema, the database user's privileges, stuck commands. It exits non-zero on a failure.
3. An existing osTicket (not this compose file): `notes/install-on-an-existing-osticket.md`.

## Before you call it production

- [ ] TLS in front, ports not published beyond loopback.
- [ ] `.env` has real secrets; `OSTICKET_ADMIN_PASSWORD` is not the default; `CORS_ORIGINS` is what you mean.
- [ ] `make doctor` is all `ok`.
- [ ] Backups of the database **and** the `osticket_config` volume, restore tried once.
- [ ] `TRUSTED_PROXY_COUNT` matches the number of proxies; `RATE_LIMIT_STORAGE_URI` set if you run more than one process.
- [ ] CI's `audit` job (pip-audit) is green: it runs on every push and weekly.
