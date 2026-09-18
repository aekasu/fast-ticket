#!/usr/bin/env bash
#
# Generates .env with safe random defaults if it doesn't exist.

set -e

ENV_FILE=".env"
EXAMPLE_FILE=".env.example"

if [ -f "$ENV_FILE" ]; then
    echo "[$ENV_FILE] already exists. Skipping auto-setup."
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

# Inject into .env using perl for cross-platform compatibility
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
# However, if they want the official setup wizard, they can change this to 'false'.
if ! grep -q "OSTICKET_AUTO_SETUP" "$ENV_FILE"; then
    echo "" >> "$ENV_FILE"
    echo "# Set to 'false' if you want to manually run the osTicket web installation wizard" >> "$ENV_FILE"
    echo "OSTICKET_AUTO_SETUP=true" >> "$ENV_FILE"
fi

echo "Successfully generated $ENV_FILE with randomized secrets!"
echo "You can now run 'make start' to boot up the stack."
