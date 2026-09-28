<?php
error_reporting(E_ALL);
ini_set('display_errors', 1);

define('OSTAPI', TRUE);
define('API_SESSION', true);

$webroot = '/var/www/html';
chdir($webroot);

require_once $webroot . '/main.inc.php';
echo "main.inc.php loaded successfully!\n";
