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

if [ ! -f "$SETUP_LOCK" ]; then
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

    # Generate ost-config.php from sample
    if [ ! -f "$CONFIG_FILE" ]; then
        cp /var/www/html/include/ost-sampleconfig.php "$CONFIG_FILE"
        chmod 0644 "$CONFIG_FILE"
    fi

    if [ "${OSTICKET_AUTO_SETUP:-true}" = "true" ]; then
        echo "[entrypoint] OSTICKET_AUTO_SETUP=true — running automated installer..."
        # Run the automated installer via PHP CLI
        php /var/www/html/setup/cli/installer.php \
            --fname="${OSTICKET_ADMIN_FIRSTNAME:-Admin}" \
            --lname="${OSTICKET_ADMIN_LASTNAME:-User}" \
            --email="${OSTICKET_ADMIN_EMAIL:-admin@example.local}" \
            --username="admin" \
            --password="${OSTICKET_ADMIN_PASSWORD:-changeme-admin}" \
            --admin-email="${OSTICKET_ADMIN_EMAIL:-admin@example.local}" \
            --db-host="${MYSQL_HOST}" \
            --db-user="${MYSQL_USER}" \
            --db-pass="${MYSQL_PASSWORD}" \
            --db-name="${MYSQL_DATABASE}" \
            --db-prefix="ost_" \
            --name="${OSTICKET_SITE_NAME:-fast-ticket Dev}" \
            || echo "[entrypoint] Installer returned non-zero (may already be installed — continuing)."

        # Remove setup directory for security (osTicket requires this)
        rm -rf /var/www/html/setup/

        # Install and enable the bridge plugin via PHP CLI
        echo "[entrypoint] Installing FastAPI integration plugin..."
        php /var/www/html/include/plugins/fastapi_integration/install.php \
            --key="${FASTAPI_INTEGRATION_KEY}" \
            || echo "[entrypoint] Plugin install returned non-zero — may already be active."

        touch "$SETUP_LOCK"
        echo "[entrypoint] Setup complete."
    else
        echo "[entrypoint] OSTICKET_AUTO_SETUP=false — skipping automated installer."
        echo "[entrypoint] Please visit http://localhost:8080 to complete the osTicket web setup wizard."
        echo "[entrypoint] After setup, install the 'fastapi_integration' plugin from the Admin Panel."
        # We don't create .setup_complete here because we want to allow the user to restart
        # the container and still have access to the setup/ directory if they haven't finished.
        # But once they finish, osTicket will force them to delete setup/ anyway.
    fi
else
    echo "[entrypoint] Setup already done (found .setup_complete)."
fi

exec "$@"
