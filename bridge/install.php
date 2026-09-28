<?php
error_reporting(E_ALL);
ini_set('display_errors', 1);

/**
 * PHP bridge plugin installer.
 * Called by the Docker entrypoint on first boot.
 *
 * Usage: php install.php --key=<integration_key>
 *
 * What it does:
 *   1. Activates the FastAPI integration plugin in osTicket's plugin registry.
 *   2. Stores the integration key in plugin config.
 *   3. Copies auth.php to the location Apache can serve it as /api/auth/integration.
 */

if (php_sapi_name() !== 'cli') {
    http_response_code(403);
    exit("CLI only.\n");
}

// Parse --key=<value> from argv
$options = getopt('', ['key:']);
$integration_key = $options['key'] ?? '';
if (empty($integration_key)) {
    fwrite(STDERR, "Error: --key=<integration_key> is required.\n");
    exit(1);
}

// Bootstrap osTicket for CLI use.
// bootstrap.php only runs Bootstrap::init() — for CLI scripts we must
// also call connect() (opens the DB) and loadCode() (loads ORM/models).
// This is the same pattern used by all osTicket CLI modules (cron, export, etc).
define('OSTAPI', TRUE);
$webroot = dirname(dirname(dirname(dirname(__FILE__))));
require_once $webroot . '/bootstrap.php';
Bootstrap::loadConfig();
Bootstrap::defineTables(TABLE_PREFIX);
Bootstrap::loadCode();
Bootstrap::connect();
require_once INCLUDE_DIR . 'class.plugin.php';

$plugin_id = 'fastapi_integration';

// ── 1. Register/enable plugin ──────────────────────────────────────────────

$existing = db_query("SELECT id FROM ost_plugin WHERE install_path LIKE '%fastapi_integration%' LIMIT 1");
if (!db_fetch_array($existing)) {
    db_query(sprintf(
        "INSERT INTO ost_plugin (name, install_path, isactive) VALUES (%s, %s, 1)",
        db_input('FastAPI Integration Bridge'),
        db_input('include/plugins/fastapi_integration/plugin.php')
    ));
    echo "[install.php] Plugin registered.\n";
} else {
    db_query("UPDATE ost_plugin SET isactive=1 WHERE install_path LIKE '%fastapi_integration%'");
    echo "[install.php] Plugin already registered — enabled.\n";
}

// ── 2. Store integration key in plugin config ──────────────────────────────

$namespace = 'fastapi_integration';
$existing_key = db_query(sprintf(
    "SELECT id FROM ost_config WHERE namespace=%s AND `key`='integration_key' LIMIT 1",
    db_input($namespace)
));
if (db_fetch_array($existing_key)) {
    db_query(sprintf(
        "UPDATE ost_config SET value=%s WHERE namespace=%s AND `key`='integration_key'",
        db_input($integration_key),
        db_input($namespace)
    ));
} else {
    db_query(sprintf(
        "INSERT INTO ost_config (namespace, `key`, value) VALUES (%s, 'integration_key', %s)",
        db_input($namespace),
        db_input($integration_key)
    ));
}
echo "[install.php] Integration key stored.\n";

// ── 3. Install auth endpoint ───────────────────────────────────────────────
// Copy auth.php to api/auth/integration so Apache serves it at /api/auth/integration
$api_auth_dir = $webroot . '/api/auth';
if (!is_dir($api_auth_dir)) {
    mkdir($api_auth_dir, 0755, true);
}
copy(dirname(__FILE__) . '/auth.php', $api_auth_dir . '/integration.php');
echo "[install.php] Auth endpoint installed at api/auth/integration.php\n";

echo "[install.php] FastAPI integration plugin installed successfully.\n";
