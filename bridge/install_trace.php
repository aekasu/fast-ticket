<?php
error_reporting(E_ALL);
ini_set('display_errors', 1);

function trace($msg) {
    echo "TRACE: $msg\n";
    flush();
}

trace("Starting install.php");

define('OSTAPI', TRUE);
$webroot = dirname(dirname(dirname(dirname(__FILE__))));

trace("Requiring bootstrap.php");
require_once $webroot . '/bootstrap.php';
trace("bootstrap.php loaded");

trace("Loading config");
Bootstrap::loadConfig();
trace("Config loaded");

trace("Defining tables");
Bootstrap::defineTables(TABLE_PREFIX);
trace("Tables defined");

trace("Connecting to DB");
Bootstrap::connect();
trace("Connected to DB");

trace("Loading code");
Bootstrap::loadCode();
trace("Code loaded");

trace("Requiring class.plugin.php");
require_once INCLUDE_DIR . 'class.plugin.php';
trace("class.plugin.php loaded");

trace("Running query");
$result = db_query("SELECT id FROM ost_plugin WHERE install_path LIKE '%fastapi_integration%' LIMIT 1", false);
trace("Query done");
if ($result && db_num_rows($result) > 0) {
    trace("Plugin already installed");
} else {
    trace("Plugin not installed");
}
