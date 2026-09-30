#!/bin/sh
# osTicket container entrypoint.
#
# Modes:
#   START_CRON=1  — run the cron daemon (osticket-cron service; the inbox fallback)
#   START_WORKER=1 — run the inbox worker (osticket-worker service; ~1 s command latency)
#   START_CRON=0  — run Apache; on first boot install osTicket headlessly
#
# Idempotency: install state is derived from the two things that actually
# define it — the database (core/schema_signature row) and the persisted
# ost-config.php (OSTINSTALLED=TRUE) — never from a marker file. Every
# combination converges to "installed" without manual `make reset`:
#
#   DB installed + config installed  -> nothing to do
#   DB installed + config missing    -> regenerate config from env (new SECRET_SALT)
#   DB empty     + config installed  -> stale config: reset it, run installer
#   DB empty     + config missing    -> run installer
#   DB half-installed (no signature) -> fail loudly; needs `make reset`
#
# ost-config.php lives in a named volume (CONFIG_STORE) and is symlinked into
# osTicket's include/ dir, so it survives image rebuilds and is never in git.

set -e

WEBROOT="/var/www/html"
# Table prefix osTicket is installed with (default: osTicket's own `ost_`).
export OSTICKET_TABLE_PREFIX="${OSTICKET_TABLE_PREFIX-ost_}"
CONFIG_FILE="$WEBROOT/include/ost-config.php"
SAMPLE_FILE="$WEBROOT/include/ost-sampleconfig.php"
CONFIG_STORE="/var/osticket-config/ost-config.php"
INSTALLED_RE="define('OSTINSTALLED',TRUE);"

config_installed() {
    grep -qF "$INSTALLED_RE" "$CONFIG_STORE" 2>/dev/null
}

# Prints one of: unreachable | empty | partial | installed
db_state() {
    php -r '
        mysqli_report(MYSQLI_REPORT_OFF);
        $c = @new mysqli(getenv("MYSQL_HOST"), getenv("MYSQL_USER"), getenv("MYSQL_PASSWORD"), getenv("MYSQL_DATABASE"));
        if ($c->connect_error) { echo "unreachable"; exit; }
        $r = $c->query("SELECT value FROM " . getenv("OSTICKET_TABLE_PREFIX") . "config WHERE namespace=\"core\" AND `key`=\"schema_signature\" LIMIT 1");
        if ($r && $r->num_rows > 0) { echo "installed"; exit; }
        $t = $c->query("SHOW TABLES LIKE \"" . getenv("OSTICKET_TABLE_PREFIX") . "config\"");
        echo ($t && $t->num_rows > 0) ? "partial" : "empty";
    ' 2>/dev/null || echo "unreachable"
}

# Write a pristine (uninstalled) config from osTicket's own sample.
reset_config() {
    cp "$SAMPLE_FILE" "$CONFIG_STORE"
    chown www-data:www-data "$CONFIG_STORE"
    chmod 0666 "$CONFIG_STORE"   # the web installer rewrites it as www-data
}

# Rebuild an *installed* config for an already-installed database.
regenerate_config() {
    php -r '
        $c = file_get_contents(getenv("SAMPLE_FILE"));
        $c = strtr($c, [
            "define(\x27OSTINSTALLED\x27,FALSE);" => "define(\x27OSTINSTALLED\x27,TRUE);",
            "%CONFIG-SIRI"    => substr(bin2hex(random_bytes(16)), 0, 32),
            "%ADMIN-EMAIL"    => getenv("OSTICKET_ADMIN_EMAIL") ?: "admin@example.local",
            "%CONFIG-DBHOST"  => getenv("MYSQL_HOST"),
            "%CONFIG-DBNAME"  => getenv("MYSQL_DATABASE"),
            "%CONFIG-DBUSER"  => getenv("MYSQL_USER"),
            "%CONFIG-DBPASS"  => getenv("MYSQL_PASSWORD"),
            "%CONFIG-PREFIX"  => getenv("OSTICKET_TABLE_PREFIX"),
        ]);
        file_put_contents(getenv("CONFIG_STORE"), $c);
    ' || return 1
    chown www-data:www-data "$CONFIG_STORE"
    chmod 0644 "$CONFIG_STORE"
}

# ── Config store: persisted file, symlinked into osTicket ─────────────────────
export SAMPLE_FILE CONFIG_STORE
mkdir -p "$(dirname "$CONFIG_STORE")"
[ -s "$CONFIG_STORE" ] || reset_config
ln -sfn "$CONFIG_STORE" "$CONFIG_FILE"

# ── Cron mode ──────────────────────────────────────────────────────────────────
if [ "${START_CRON:-0}" = "1" ]; then
    echo "[entrypoint] Starting cron daemon..."
    exec cron -f
fi

# ── Worker mode ────────────────────────────────────────────────────────────────
# Runs as www-data (like the web and cron paths, so files osTicket writes stay owned by it)
# and is exec'd directly, so a container stop reaches PHP as SIGTERM.
if [ "${START_WORKER:-0}" = "1" ]; then
    echo "[entrypoint] Starting inbox worker..."
    exec setpriv --reuid=www-data --regid=www-data --init-groups \
        php "$WEBROOT/include/plugins/fastapi_integration/worker.php"
fi

# ── Manual-install mode ────────────────────────────────────────────────────────
if [ "${OSTICKET_AUTO_SETUP:-true}" != "true" ]; then
    echo "[entrypoint] OSTICKET_AUTO_SETUP=false — skipping automated installer."
    echo "[entrypoint] Complete the web wizard at http://localhost:8080/setup/, then run:"
    echo "[entrypoint]   docker compose exec osticket php $WEBROOT/include/plugins/fastapi_integration/install.php --key=<key>"
    exec "$@"
fi

# ── Wait for the database ──────────────────────────────────────────────────────
echo "[entrypoint] Waiting for MySQL at ${MYSQL_HOST}:3306..."
STATE=unreachable
for i in $(seq 1 60); do
    STATE=$(db_state)
    [ "$STATE" != "unreachable" ] && break
    echo "[entrypoint] MySQL not ready yet (attempt $i/60)..."
    sleep 2
done
if [ "$STATE" = "unreachable" ]; then
    echo "[entrypoint] FATAL: cannot connect to MySQL at ${MYSQL_HOST}." >&2
    exit 1
fi

if config_installed; then CFG=installed; else CFG=missing; fi
echo "[entrypoint] Install state: database=$STATE config=$CFG"

# ── Converge to "installed" ────────────────────────────────────────────────────
run_installer() {
    echo "[entrypoint] Starting Apache for the web installer..."
    apache2-foreground &
    APACHE_PID=$!

    for i in $(seq 1 30); do
        curl -sf http://localhost/setup/install.php -o /dev/null 2>/dev/null && break
        echo "[entrypoint] Waiting for Apache (attempt $i/30)..."
        sleep 2
    done

    # The admin username must NOT be: admin, admins, username, osticket
    # (osTicket's installer rejects them — see setup/inc/class.installer.php)
    INSTALL_USERNAME="${OSTICKET_ADMIN_USERNAME:-helpdesk}"
    echo "[entrypoint] Running osTicket installer (admin username: ${INSTALL_USERNAME})..."
    curl -s \
        -X POST \
        --data-urlencode "s=install" \
        --data-urlencode "name=${OSTICKET_SITE_NAME:-fast-ticket Dev}" \
        --data-urlencode "email=${OSTICKET_SYSTEM_EMAIL:-support@example.local}" \
        --data-urlencode "fname=${OSTICKET_ADMIN_FIRSTNAME:-Admin}" \
        --data-urlencode "lname=${OSTICKET_ADMIN_LASTNAME:-User}" \
        --data-urlencode "admin_email=${OSTICKET_ADMIN_EMAIL:-admin@example.local}" \
        --data-urlencode "username=${INSTALL_USERNAME}" \
        --data-urlencode "passwd=${OSTICKET_ADMIN_PASSWORD:-changeme-admin}" \
        --data-urlencode "passwd2=${OSTICKET_ADMIN_PASSWORD:-changeme-admin}" \
        --data-urlencode "prefix=${OSTICKET_TABLE_PREFIX}" \
        --data-urlencode "dbhost=${MYSQL_HOST}" \
        --data-urlencode "dbname=${MYSQL_DATABASE}" \
        --data-urlencode "dbuser=${MYSQL_USER}" \
        --data-urlencode "dbpass=${MYSQL_PASSWORD}" \
        http://localhost/setup/install.php > /tmp/install_response.html 2>&1 || true

    kill "$APACHE_PID" 2>/dev/null || true
    wait "$APACHE_PID" 2>/dev/null || true

    # Authoritative success signal: installer flipped the config AND the DB has its signature.
    if config_installed && [ "$(db_state)" = "installed" ]; then
        chmod 0644 "$CONFIG_STORE"   # osTicket wants a read-only config after install
        echo "[entrypoint] osTicket installation succeeded."
    else
        echo "[entrypoint] FATAL: osTicket installer did not complete (config=$(config_installed && echo installed || echo missing), database=$(db_state))." >&2
        echo "[entrypoint] Installer response: docker compose exec osticket cat /tmp/install_response.html" >&2
        exit 1
    fi
}

case "$STATE/$CFG" in
    installed/installed)
        echo "[entrypoint] Already installed — nothing to do."
        ;;
    installed/missing)
        echo "[entrypoint] WARNING: database is installed but ost-config.php is missing/uninstalled."
        echo "[entrypoint] Regenerating it from env (a new SECRET_SALT is generated; values encrypted"
        echo "[entrypoint] with the old salt, e.g. stored mail passwords, will need to be re-entered)."
        regenerate_config
        ;;
    empty/installed)
        echo "[entrypoint] Stale ost-config.php (database is empty) — resetting it and installing."
        reset_config
        run_installer
        ;;
    empty/missing)
        reset_config
        run_installer
        ;;
    partial/*)
        echo "[entrypoint] FATAL: database has osTicket tables but no install signature (interrupted install)." >&2
        echo "[entrypoint] Run: make reset && make start" >&2
        exit 1
        ;;
esac

# The installer's setup/ dir must not remain (osTicket nags/blocks while it exists).
rm -rf "$WEBROOT/setup/"

# ── Bridge plugin (idempotent: registers/enables, stores key, installs auth endpoint) ──
echo "[entrypoint] Installing FastAPI integration plugin..."
php "$WEBROOT/include/plugins/fastapi_integration/install.php" \
    --key="${FASTAPI_INTEGRATION_KEY}" \
    || { echo "[entrypoint] FATAL: bridge plugin install failed." >&2; exit 1; }

echo "[entrypoint] Setup complete."
exec "$@"
