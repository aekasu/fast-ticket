#!/bin/sh
# osTicket container entrypoint.
# Handles two modes:
#   START_CRON=1  — run the cron daemon (osticket-cron service)
#   START_CRON=0  — run Apache and perform first-boot osTicket setup

set -e

# ── Cron mode ──────────────────────────────────────────────────────────────────
if [ "${START_CRON:-0}" = "1" ]; then
    echo "[entrypoint] Starting cron daemon..."
    exec cron -f
fi

# ── First-boot osTicket installation ──────────────────────────────────────────
CONFIG_FILE="/var/www/html/include/ost-config.php"
SETUP_LOCK="/var/www/html/.setup_complete"

# ── Sanity check on restart: lock exists but config doesn't reflect install ───
if [ -f "$SETUP_LOCK" ]; then
    if ! grep -q "define('OSTINSTALLED',TRUE);" "$CONFIG_FILE" 2>/dev/null; then
        echo "[entrypoint] WARNING: .setup_complete exists but OSTINSTALLED is not TRUE in config."
        echo "[entrypoint] Stale lock detected — removing and re-running setup..."
        rm -f "$SETUP_LOCK"
    else
        echo "[entrypoint] Setup already done (found .setup_complete — OSTINSTALLED=TRUE confirmed)."
        rm -rf /var/www/html/setup/
        exec "$@"
    fi
fi

# ── First boot (or stale lock cleared above) ──────────────────────────────────
echo "[entrypoint] First boot detected."

# Wait for MySQL to be ready
echo "[entrypoint] Waiting for MySQL at ${MYSQL_HOST}:3306..."
for i in $(seq 1 60); do
    if php -r "
        \$conn = new mysqli('${MYSQL_HOST}', '${MYSQL_USER}', '${MYSQL_PASSWORD}', '${MYSQL_DATABASE}');
        if (\$conn->connect_error) { exit(1); } exit(0);
    " 2>/dev/null; then
        echo "[entrypoint] MySQL is ready."
        break
    fi
    echo "[entrypoint] MySQL not ready yet (attempt $i/60)..."
    sleep 2
done

# Ensure ost-config.php is writable (it's bind-mounted from the host).
# The installer will write to it on success.
if [ ! -f "$CONFIG_FILE" ]; then
    cp /var/www/html/include/ost-sampleconfig.php "$CONFIG_FILE"
fi
chmod 0666 "$CONFIG_FILE"
chown www-data:www-data "$CONFIG_FILE"

if [ "${OSTICKET_AUTO_SETUP:-true}" = "true" ]; then

    # ── Detect if osTicket DB tables already exist (e.g. volume survived restart) ──
    DB_INSTALLED=$(php -r "
        \$c = new mysqli('${MYSQL_HOST}', '${MYSQL_USER}', '${MYSQL_PASSWORD}', '${MYSQL_DATABASE}');
        if (\$c->connect_error) { echo 'no'; exit; }
        \$r = \$c->query('SELECT value FROM ost_config WHERE namespace=\"core\" AND \`key\`=\"schema_signature\" LIMIT 1');
        echo (\$r && \$r->num_rows > 0) ? 'yes' : 'no';
    " 2>/dev/null || echo "no")

    if [ "$DB_INSTALLED" = "yes" ] && grep -q "define('OSTINSTALLED',TRUE);" "$CONFIG_FILE" 2>/dev/null; then
        echo "[entrypoint] Database and config both indicate osTicket is already installed. Skipping web installer."

    elif [ "$DB_INSTALLED" = "yes" ] && ! grep -q "define('OSTINSTALLED',TRUE);" "$CONFIG_FILE" 2>/dev/null; then
        echo "[entrypoint] WARNING: Database appears installed but config has OSTINSTALLED=FALSE."
        echo "[entrypoint] The config file may have been reset. Attempting to repair..."
        # The only safe repair path is to re-run the installer (it will detect the prefix clash
        # and bail with an error). Instead, flag this as a broken state that requires manual reset.
        echo "[entrypoint] FATAL: Inconsistent state — DB has osTicket tables but config is uninstalled."
        echo "[entrypoint] Run: docker compose down -v && make start   to reset from scratch."
        exit 1

    else
        # ── Fresh install via headless HTTP POST to the web installer ─────────────
        echo "[entrypoint] OSTICKET_AUTO_SETUP=true — starting Apache for web installer..."

        # Start Apache in the background so the installer endpoint is reachable.
        apache2-foreground &
        APACHE_PID=$!

        # Wait for Apache to accept connections.
        echo "[entrypoint] Waiting for Apache to come up..."
        for i in $(seq 1 30); do
            if curl -sf http://localhost/setup/install.php -o /dev/null 2>/dev/null; then
                echo "[entrypoint] Apache is up."
                break
            fi
            echo "[entrypoint] Waiting for Apache (attempt $i/30)..."
            sleep 2
        done

        # The admin username must NOT be: admin, admins, username, osticket
        # (osTicket's installer explicitly rejects these — see setup/inc/class.installer.php)
        INSTALL_USERNAME="${OSTICKET_ADMIN_USERNAME:-helpdesk}"

        echo "[entrypoint] Running osTicket web installer (username: ${INSTALL_USERNAME})..."
        curl -sf \
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
            --data-urlencode "prefix=ost_" \
            --data-urlencode "dbhost=${MYSQL_HOST}" \
            --data-urlencode "dbname=${MYSQL_DATABASE}" \
            --data-urlencode "dbuser=${MYSQL_USER}" \
            --data-urlencode "dbpass=${MYSQL_PASSWORD}" \
            http://localhost/setup/install.php > /tmp/install_response.html 2>&1 || true

        # Verify the installer actually succeeded by checking whether it wrote
        # OSTINSTALLED=TRUE to the config file on disk. This is the authoritative signal.
        if grep -q "define('OSTINSTALLED',TRUE);" "$CONFIG_FILE" 2>/dev/null; then
            echo "[entrypoint] osTicket installation succeeded (OSTINSTALLED=TRUE confirmed in config)."
            kill $APACHE_PID 2>/dev/null || true
            wait $APACHE_PID 2>/dev/null || true
        else
            echo "[entrypoint] FATAL: osTicket web installer did not update config (OSTINSTALLED still FALSE)."
            echo "[entrypoint] Installer response saved to /tmp/install_response.html"
            echo "[entrypoint] Inspect with: docker compose exec osticket cat /tmp/install_response.html"
            kill $APACHE_PID 2>/dev/null || true
            wait $APACHE_PID 2>/dev/null || true
            exit 1  # Do NOT touch SETUP_LOCK — fail loudly so the container restarts cleanly
        fi

        # Remove setup directory for security (osTicket requires this post-install)
        rm -rf /var/www/html/setup/
    fi

    # Remove setup dir in the already-installed path too (may still be present)
    rm -rf /var/www/html/setup/

    # ── Install and enable the bridge plugin via PHP CLI ──────────────────────
    echo "[entrypoint] Installing FastAPI integration plugin..."
    php /var/www/html/include/plugins/fastapi_integration/install.php \
        --key="${FASTAPI_INTEGRATION_KEY}" \
        || {
            echo "[entrypoint] FATAL: Bridge plugin install failed. Aborting."
            exit 1   # Do NOT touch SETUP_LOCK
        }

    # Only record success after BOTH osTicket installation AND bridge plugin succeed.
    touch "$SETUP_LOCK"
    echo "[entrypoint] Setup complete."

else
    echo "[entrypoint] OSTICKET_AUTO_SETUP=false — skipping automated installer."
    echo "[entrypoint] Please visit http://localhost:8080/setup/ to complete the osTicket web setup wizard."
    echo "[entrypoint] After setup, install the 'fastapi_integration' plugin from the Admin Panel."
    echo "[entrypoint] Then run the bridge plugin installer manually:"
    echo "[entrypoint]   docker compose exec osticket php /var/www/html/include/plugins/fastapi_integration/install.php --key=<key>"
fi

exec "$@"
