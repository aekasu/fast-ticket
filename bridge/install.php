<?php
error_reporting(E_ALL & ~E_DEPRECATED & ~E_STRICT);  // osTicket 1.18 emits PHP 8.2 deprecations
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

// osTicket's PluginManager wants install_path to be the plugin *directory*
// relative to include/ (it appends /plugin.php itself, includes the manifest it
// returns, then instantiates the class the manifest names). Anything else and
// the plugin is silently never loaded, so its cron/model signals never connect.
$install_path = 'plugins/fastapi_integration';
$version = '1.0';   // keep in sync with the manifest at the bottom of plugin.php

$existing = db_query("SELECT id FROM ost_plugin WHERE install_path LIKE '%fastapi_integration%' LIMIT 1");
if (!db_fetch_array($existing)) {
    db_query(sprintf(
        "INSERT INTO ost_plugin (name, install_path, isactive, version, installed) VALUES (%s, %s, 1, %s, NOW())",
        db_input('FastAPI Integration Bridge'),
        db_input($install_path),
        db_input($version)
    ));
    echo "[install.php] Plugin registered.\n";
} else {
    // Also repairs rows written by older versions of this script (wrong install_path).
    db_query(sprintf(
        "UPDATE ost_plugin SET isactive=1, install_path=%s, version=%s WHERE install_path LIKE '%%fastapi_integration%%'",
        db_input($install_path),
        db_input($version)
    ));
    echo "[install.php] Plugin already registered — enabled.\n";
}

// osTicket bootstraps a plugin only through an enabled *instance* row, so make
// sure exactly one exists and is enabled (flags bit 0x1 = PluginInstance::FLAG_ENABLED).
$plugin_row = db_fetch_array(db_query("SELECT id FROM ost_plugin WHERE install_path=" . db_input($install_path) . " LIMIT 1"));
$plugin_db_id = (int) $plugin_row['id'];
$instance = db_fetch_array(db_query("SELECT id FROM ost_plugin_instance WHERE plugin_id=$plugin_db_id LIMIT 1"));
if (!$instance) {
    db_query(sprintf(
        "INSERT INTO ost_plugin_instance (plugin_id, flags, name, created) VALUES (%d, 1, %s, NOW())",
        $plugin_db_id,
        db_input('FastAPI Integration Bridge')
    ));
    echo "[install.php] Plugin instance created.\n";
} else {
    db_query("UPDATE ost_plugin_instance SET flags = flags | 1 WHERE plugin_id=$plugin_db_id");
    echo "[install.php] Plugin instance already present — enabled.\n";
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
