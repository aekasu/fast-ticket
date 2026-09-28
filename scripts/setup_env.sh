#!/usr/bin/env bash
#
# Generates .env with safe random defaults if it doesn't exist.

set -e

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"

# ── ost-config.php ────────────────────────────────────────────────────────────
# The bind-mount target must exist on the host before `docker compose up`.
# It must contain the sample placeholder content (not be empty) so the web
# installer can find and rewrite it. After successful installation, the
# installer overwrites it with OSTINSTALLED=TRUE and real DB credentials.
CONFIG_FILE="docker/osticket/ost-config.php"
SAMPLE_FILE="docker/osticket/ost-sampleconfig.php"

if [ ! -s "$CONFIG_FILE" ]; then
    echo "[$CONFIG_FILE] missing or empty — initializing from sample..."
    if [ -f "$SAMPLE_FILE" ]; then
        cp "$SAMPLE_FILE" "$CONFIG_FILE"
    else
        # The committed ost-config.php is already in sample format (%CONFIG-* tokens).
        # If it's somehow missing, something is wrong with the checkout.
        echo "ERROR: Neither $CONFIG_FILE nor $SAMPLE_FILE found." >&2
        echo "       Ensure you have a clean checkout of the repository." >&2
        exit 1
    fi
    chmod 0666 "$CONFIG_FILE"
fi

# ── .env ──────────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    echo "[$ENV_FILE] already exists. Skipping env generation."
    exit 0
fi

echo "[$ENV_FILE] not found. Creating from $EXAMPLE_FILE..."
cp "$EXAMPLE_FILE" "$ENV_FILE"

# Function to generate a random 32-char hex string
gen_secret() {
    openssl rand -hex 16
}

# Generate secrets
MYSQL_ROOT_PW=$(gen_secret)
MYSQL_PW=$(gen_secret)
FASTAPI_KEY=$(gen_secret)
JWT_SECRET=$(gen_secret)

# Inject into .env using sed
if [[ "$OSTYPE" == "darwin"* ]]; then
    SED_INPLACE="sed -i ''"
else
    SED_INPLACE="sed -i"
fi

$SED_INPLACE "s/^MYSQL_ROOT_PASSWORD=.*/MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PW}/" "$ENV_FILE"
$SED_INPLACE "s/^MYSQL_PASSWORD=.*/MYSQL_PASSWORD=${MYSQL_PW}/" "$ENV_FILE"
$SED_INPLACE "s/^FASTAPI_INTEGRATION_KEY=.*/FASTAPI_INTEGRATION_KEY=${FASTAPI_KEY}/" "$ENV_FILE"
$SED_INPLACE "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=${JWT_SECRET}/" "$ENV_FILE"

# By default, we auto-setup osTicket so users don't have to go through the web wizard.
if ! grep -q "OSTICKET_AUTO_SETUP" "$ENV_FILE"; then
    echo "" >> "$ENV_FILE"
    echo "# Set to 'false' if you want to manually run the osTicket web installation wizard" >> "$ENV_FILE"
    echo "OSTICKET_AUTO_SETUP=true" >> "$ENV_FILE"
fi

echo "Successfully generated $ENV_FILE with randomized secrets!"
echo "You can now run 'make start' to boot up the stack."
