<?php
error_reporting(E_ALL);
ini_set('display_errors', 1);

function trace($msg) {
    echo "TRACE: $msg\n";
    flush();
}

trace("Starting integration.php");

define('OSTAPI', TRUE);
$__webroot = dirname(dirname(dirname(__FILE__)));
trace("Webroot: " . $__webroot);

trace("Requiring bootstrap.php");
require_once $__webroot . '/bootstrap.php';
trace("bootstrap.php loaded");

define('API_SESSION', true);
trace("Requiring main.inc.php");
require_once $__webroot . '/main.inc.php';
trace("main.inc.php loaded");

trace("Requiring class.staff.php");
require_once INCLUDE_DIR . 'class.staff.php';
trace("class.staff.php loaded");

trace("Checking auth");
$headers = getallheaders();
trace("Auth complete");
