<?php
/**
 * Inbox worker — drains ost_integration_inbox continuously instead of once a minute.
 *
 * Optional: osTicket's own cron keeps working as the fallback (the claim in
 * FastApiIntegrationPlugin::drainInbox() is atomic, so the two can run together).
 * This only lowers the latency of a queued command from up to 60 s to about a second.
 *
 * It bootstraps osTicket exactly as api/cron.php does — no osTicket file is changed —
 * and calls the plugin's drain method in a loop.
 *
 * One command per process: osTicket keeps process-wide static state (TaskForm::$instance
 * keeps the previous task's answers, ...), so after any command has run this worker
 * re-executes itself in place (pcntl_exec: same PID, so the supervisor never notices)
 * instead of running the next one in a dirty process. While idle it just sleeps and polls.
 * It exits (0) after FASTAPI_WORKER_MAX_SECONDS, after FASTAPI_WORKER_MAX_COMMANDS commands (default 100 —
 * each re-exec leaks one file descriptor, so the chain is bounded), or when memory grows, and on any
 * unexpected error (1), so the supervisor (docker `restart: unless-stopped`, systemd
 * `Restart=always`) gives it a fresh process and database connection.
 *
 * Usage: php include/plugins/fastapi_integration/worker.php          (loop)
 *        php include/plugins/fastapi_integration/worker.php --one    (run at most one command;
 *                                                                     exit 10 if it ran one, else 0 —
 *                                                                     what osTicket's cron fallback uses)
 */

if (php_sapi_name() !== 'cli') {
    http_response_code(403);
    exit("CLI only.\n");
}

error_reporting(E_ALL & ~E_DEPRECATED & ~E_STRICT);  // osTicket 1.18 emits PHP 8.2 deprecations

// include/plugins/fastapi_integration/worker.php -> the osTicket web root is four levels up.
$webroot = dirname(dirname(dirname(dirname(__FILE__))));
@chdir($webroot . '/api');
require 'api.inc.php';   // the bootstrap api/cron.php uses: main.inc.php + plugin bootstrap

require_once INCLUDE_DIR . 'class.plugin.php';
if (!class_exists('FastApiIntegrationPlugin'))
    require_once dirname(__FILE__) . '/plugin.php';

FastApiIntegrationPlugin::ensureTables();
$plugin = new FastApiIntegrationPlugin();

if (in_array('--one', $argv ?? [], true)) {
    try {
        exit($plugin->drainInbox(1) ? 10 : 0);
    } catch (Throwable $e) {
        error_log('[FastAPI Integration] worker --one error: ' . $e->getMessage());
        exit(1);
    }
}

$max_seconds = (int) (getenv('FASTAPI_WORKER_MAX_SECONDS') ?: 900);
// Commands run since the supervisor last started this process (carried across the re-execs as --run=N).
// PHP keeps its own script file open across pcntl_exec, so every re-exec leaks one descriptor; after this
// many commands the worker exits instead and the supervisor starts a fresh process with a clean table.
$max_commands = (int) (getenv('FASTAPI_WORKER_MAX_COMMANDS') ?: 100);
$run          = (int) (getopt('', ['run:'])['run'] ?? 0);
$idle_usleep = (int) (getenv('FASTAPI_WORKER_IDLE_MS') ?: 500) * 1000;
$max_memory  = 192 * 1024 * 1024;
$started     = time();
$stop        = false;

if (function_exists('pcntl_async_signals')) {
    pcntl_async_signals(true);
    foreach ([SIGTERM, SIGINT] as $signal)
        pcntl_signal($signal, function () use (&$stop) { $stop = true; });
}

error_log('[FastAPI Integration] Inbox worker started.');

while (!$stop && (time() - $started) < $max_seconds && memory_get_usage(true) < $max_memory) {
    try {
        $ran = $plugin->drainInbox();
    } catch (Throwable $e) {
        // A dead DB connection or a bug outside a handler: don't spin on it, restart clean.
        error_log('[FastAPI Integration] Inbox worker error, exiting: ' . $e->getMessage());
        exit(1);
    }
    if ($ran) {
        // Close the database connection FIRST. pcntl_exec replaces the process image but keeps every open
        // descriptor, and a mysqli socket is not close-on-exec: without this each command leaked one
        // connection until the worker recycled, and a busy run exhausted max_connections — the next web
        // request (a login) then got a 500.
        db_close();
        // Start the next command in a clean process (see the header) — unless it is time to recycle.
        if (function_exists('pcntl_exec') && $run + 1 < $max_commands)
            pcntl_exec(PHP_BINARY, [__FILE__, '--run=' . ($run + 1)]);
        error_log('[FastAPI Integration] Inbox worker exiting (' . ($run + 1) . ' commands: recycle).');
        exit(0);   // no pcntl, or time to recycle: the supervisor restarts us
    }
    usleep($idle_usleep);
}

error_log('[FastAPI Integration] Inbox worker exiting (' . ($stop ? 'signal' : 'recycle') . ').');
exit(0);
