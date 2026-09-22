<?php
/**
 * FastAPI Integration - High-Speed Queue Processor Daemon
 * 
 * This script runs as a long-running CLI process to constantly monitor the
 * IntegrationInbox for new commands from FastAPI. By running this script
 * instead of relying on the 1-minute cron, queue processing latency is 
 * reduced to milliseconds.
 */

if (php_sapi_name() !== 'cli') {
    die("This script must be run from the command line.");
}

// Locate the root osTicket directory and bootstrap the environment.
// bootstrap.php is the CLI-safe entry point; main.inc.php is for HTTP requests.
$osTicketRootDir = realpath(__DIR__ . '/../../../');
if (!file_exists($osTicketRootDir . '/bootstrap.php')) {
    die("Unable to locate bootstrap.php. Please check the directory structure.\n");
}

// Define the INCLUDE_DIR constant the same way osTicket's own CLI scripts do.
define('INCLUDE_DIR', $osTicketRootDir . '/include/');

require_once($osTicketRootDir . '/bootstrap.php');
require_once(__DIR__ . '/integration.php');

echo "[*] FastAPI Integration Daemon starting up...\n";

// Directly instantiate the plugin. PluginManager::allActive() only works
// inside a bootstrapped HTTP request where plugins have been registered;
// from the CLI it returns an empty iterator, causing the daemon to exit
// immediately with a false-negative "not installed" error.
$plugin = new FastApiIntegrationPlugin();
// Run the schema setup once at daemon startup so any missing columns are
// created before we start consuming messages.
$plugin->bootstrap();

echo "[*] Connected to osTicket environment. Entering polling loop...\n";

// Maximum memory allowed before we gracefully exit and let the process 
// manager (e.g., Docker/Systemd) restart the script. (64MB)
$maxMemoryBytes = 64 * 1024 * 1024; 

$cycles = 0;

while (true) {
    try {
        // Process the inbox
        $plugin->processInbox();
    } catch (Throwable $e) {
        // Catch any fatal errors during processing to keep the daemon alive
        error_log("[FastAPI Daemon Error] " . $e->getMessage());
        echo "[-] Error during cycle: " . $e->getMessage() . "\n";
    }

    // Sleep for 250 milliseconds (250,000 microseconds)
    usleep(250000);
    
    $cycles++;
    
    // Check memory every 40 cycles (approx every 10 seconds)
    if ($cycles % 40 === 0) {
        $memUsage = memory_get_usage(true);
        if ($memUsage > $maxMemoryBytes) {
            echo "[!] Memory threshold exceeded (" . round($memUsage/1024/1024, 2) . " MB). Exiting gracefully for restart.\n";
            exit(0);
        }
    }
}
