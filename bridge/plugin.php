<?php
/**
 * FastAPI Integration Plugin for osTicket
 *
 * Bridges the fast-ticket FastAPI layer with osTicket's PHP model layer.
 * Consumes " . FASTAPI_INBOX_TABLE . " (commands from FastAPI) and produces
 * " . FASTAPI_OUTBOX_TABLE . " (events for the Python outbox worker).
 *
 * Architecture:
 *   FastAPI → " . FASTAPI_INBOX_TABLE . " → [this plugin via cron] → osTicket PHP models
 *   osTicket PHP models → " . FASTAPI_OUTBOX_TABLE . " → Python outbox worker → WebSocket
 *
 * The plugin hooks into osTicket's Signal system ('cron') to drain the inbox
 * every minute via api/cron.php.
 */

require_once INCLUDE_DIR . 'class.plugin.php';

// Our two tables follow osTicket's install-time table prefix, like its own (CONFIG_TABLE, ...).
if (!defined('FASTAPI_INBOX_TABLE')) {
    define('FASTAPI_INBOX_TABLE', TABLE_PREFIX . 'integration_inbox');
    define('FASTAPI_OUTBOX_TABLE', TABLE_PREFIX . 'integration_outbox');
}

/**
 * The plugin's admin-panel settings (Admin Panel → Manage → Plugins → FastAPI Integration).
 *
 * osTicket only bootstraps a plugin through an enabled instance whose config object
 * resolves (PluginInstance::bootstrap), so a PluginConfig subclass is required even
 * with no settings. The one setting is the shared integration key; see
 * FastApiIntegrationPlugin::integrationKey() for where else it can come from.
 */
class FastApiIntegrationPluginConfig extends PluginConfig {
    function getOptions() {
        return array(
            'integration_key' => new TextboxField(array(
                'label' => 'Integration key',
                'hint'  => 'Shared secret between osTicket and fast-ticket (at least 32 characters). '
                         . 'Leave blank to generate one, save, then copy it into fast-ticket as '
                         . 'FASTAPI_INTEGRATION_KEY. Not needed here if the web server and the worker '
                         . 'already have FASTAPI_INTEGRATION_KEY in their environment.',
                'configuration' => array('size' => 64, 'length' => 128),
            )),
        );
    }

    function pre_save(&$config, &$errors) {
        $key = trim((string) ($config['integration_key'] ?? ''));
        if ($key === '')
            $key = bin2hex(random_bytes(24));
        elseif (strlen($key) < 32) {
            $errors['err'] = 'The integration key must be at least 32 characters (or leave it blank to generate one).';
            return false;
        }
        $config['integration_key'] = $key;
        return true;
    }
}

class FastApiIntegrationPlugin extends Plugin {

    const VERSION = '1.0';   // keep in sync with the manifest below and install.php

    var $config_class = 'FastApiIntegrationPluginConfig';

    /**
     * The shared integration key: the environment (compose, systemd) first, then the value
     * saved on the plugin's admin-panel settings page, then the legacy row install.php writes.
     */
    static function integrationKey() {
        $key = getenv('FASTAPI_INTEGRATION_KEY') ?: ($_SERVER['FASTAPI_INTEGRATION_KEY'] ?? '');
        if ($key)
            return $key;
        // Plugin settings live in the config table under plugin.<plugin id>.instance.<instance id>.
        $res = db_query(
            "SELECT c.value FROM " . CONFIG_TABLE . " c"
            . " JOIN " . PLUGIN_INSTANCE_TABLE . " i ON c.namespace = CONCAT('plugin.', i.plugin_id, '.instance.', i.id)"
            . " JOIN " . PLUGIN_TABLE . " p ON p.id = i.plugin_id"
            . " WHERE p.install_path LIKE '%fastapi_integration%' AND c.`key`='integration_key' AND c.value <> ''"
            . " LIMIT 1");
        if ($res && ($row = db_fetch_array($res)))
            return $row['value'];
        $res = db_query("SELECT value FROM " . CONFIG_TABLE . " WHERE namespace='fastapi_integration' AND `key`='integration_key' LIMIT 1");
        $row = $res ? db_fetch_array($res) : null;
        return $row ? $row['value'] : '';
    }

    /**
     * Create the plugin's two tables if they are missing (CREATE ... IF NOT EXISTS from
     * schema.php). Called when the plugin is enabled or upgraded in the admin panel, by
     * install.php, and at the start of every worker — never per web request.
     */
    static function ensureTables() {
        $exists = function () {
            foreach ([FASTAPI_INBOX_TABLE, FASTAPI_OUTBOX_TABLE] as $table) {
                $res = db_query('SHOW TABLES LIKE ' . db_input($table));
                if (!$res || !db_num_rows($res))
                    return false;
            }
            return true;
        };
        if ($exists())
            return;
        foreach (require dirname(__FILE__) . '/schema.php' as $sql) {
            db_query(strtr($sql, [
                '{inbox}'  => FASTAPI_INBOX_TABLE,
                '{outbox}' => FASTAPI_OUTBOX_TABLE,
            ]));
        }
        if (!$exists())
            throw new Exception('Could not create ' . FASTAPI_INBOX_TABLE . ' / ' . FASTAPI_OUTBOX_TABLE
                . ' — grant the database user CREATE, or run the statements in schema.php by hand.');
    }

    function enable() {
        self::ensureTables();
        return true;
    }

    function pre_upgrade(&$errors) {
        try {
            self::ensureTables();
        } catch (Throwable $e) {
            $errors['err'] = $e->getMessage();
            return false;
        }
        return true;
    }

    /**
     * Called by osTicket for each enabled plugin instance (see install.php,
     * which creates one). Connect every signal here: signals connected in the
     * constructor were observed not to be registered when osTicket loads the
     * plugin from the database.
     */
    function bootstrap() {
        Signal::connect('cron', array($this, 'processInbox'));
        Signal::connect('api', array($this, 'registerApiEndpoints'));

        // Outbound event synchronization
        Signal::connect('model.created', array($this, 'onModelCreated'));
        Signal::connect('model.updated', array($this, 'onModelUpdated'));
        Signal::connect('model.deleted', array($this, 'onModelDeleted'));

        Signal::connect('object.created', array($this, 'onObjectCreated'));
        Signal::connect('object.edited', array($this, 'onObjectEdited'));
        Signal::connect('object.deleted', array($this, 'onObjectDeleted'));
    }

    /**
     * Drain the integration inbox — called every minute by cron.
     *
     * Claims rows atomically (status = pending → processing), dispatches to
     * the appropriate handler, then writes a result event to the outbox.
     */
    function processInbox() {
        if (!self::integrationKey()) {
            error_log('[FastAPI Integration] Plugin not configured — skipping inbox drain.');
            return;
        }
        // One command per fresh PHP process. osTicket keeps process-wide static state (e.g.
        // TaskForm::$instance holds the previous task's answers), so a second command run in
        // this process could inherit the first one's data — it did: every task created after
        // the first got the first task's title. worker.php --one runs a single command and
        // exits 10 if it ran one, 0 if the inbox was empty.
        $worker = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(dirname(__FILE__) . '/worker.php') . ' --one';
        for ($i = 0; $i < 50; $i++) {
            $code = 0;
            passthru($worker, $code);
            if ($code !== 10)
                break;
        }
    }

    /**
     * Claim and run up to ``$limit`` pending commands; returns how many were run.
     * Used by worker.php (limit 1: see processInbox() for why). The claim is atomic, so
     * the worker and osTicket's cron can run at once without executing a command twice.
     */
    function drainInbox($limit = 1) {
        // The key is provisioned by install.php (ost_config) and mirrored in the container env.
        if (!self::integrationKey()) {
            error_log('[FastAPI Integration] Plugin not configured — skipping inbox drain.');
            return 0;
        }

        $batch_size = max(1, (int) $limit);

        // ── Stuck-row recovery ───────────────────────────────────────────────
        // A row is 'processing' only while a run holds it, and processed_at doubles as
        // the claim time until the row finishes (see the claim below). One held for over
        // 5 minutes belongs to a run that died (fatal, OOM, killed). Fail it rather than
        // retry: it may have partly executed, and re-running a non-idempotent command
        // (ticket.create, ...) would duplicate work — and a handler that always crashes
        // would otherwise be retried forever.
        db_query("UPDATE " . FASTAPI_INBOX_TABLE . "
                   SET status='failed', processed_at=NOW(),
                       error_message='Timed out while processing (the handler crashed or the run was killed); not retried automatically.'
                   WHERE status='processing'
                     AND COALESCE(processed_at, created_at) < DATE_SUB(NOW(), INTERVAL 5 MINUTE)");

        // ── Atomic claim ─────────────────────────────────────────────────────
        // Generate a unique run ID for this invocation so concurrent cron runs
        // can't steal each other's rows.
        $run_id = db_input(bin2hex(random_bytes(8)));

        db_query("UPDATE " . FASTAPI_INBOX_TABLE . "
                   SET status='processing', claimed_by={$run_id}, processed_at=NOW()
                   WHERE status='pending'
                   ORDER BY id ASC
                   LIMIT {$batch_size}");

        // Only fetch rows we own (identified by our run_id).
        $rows = db_query("SELECT * FROM " . FASTAPI_INBOX_TABLE . "
                          WHERE status='processing' AND claimed_by={$run_id}
                          ORDER BY id ASC");

        $ran = 0;
        while ($row = db_fetch_array($rows)) {
            $this->dispatchCommand($row);
            $ran++;
        }
        return $ran;
    }

    /**
     * Route a single inbox command to the appropriate handler.
     */
    private function dispatchCommand($row) {
        $payload = json_decode($row['payload'], true) ?: [];
        $command_type = $row['command_type'];
        $inbox_id = $row['id'];

        error_log("[FastAPI Integration] Processing command #{$inbox_id}: {$command_type}");

        self::$runningCommand = true;
        try {
            // Act as the staff member who made the API request. osTicket's models read the
            // global $thisstaff for permission checks and to attribute replies, notes and
            // assignments (Ticket, Task, Thread) — and some refuse outright without it
            // (e.g. Team::delete() returns false). There is no session under cron, so it
            // would otherwise be null.
            // Each command must see the database as it is now, not the entities an earlier command
            // in this same cron run left in osTicket's ORM identity cache.
            if (class_exists('ModelInstanceManager'))
                ModelInstanceManager::flushCache();

            global $thisstaff;
            $thisstaff = null;
            if ($row['requested_by_staff_id']) {
                $thisstaff = Staff::lookup((int) $row['requested_by_staff_id']);
                if (!$thisstaff || !$thisstaff->isActive()) {
                    throw new Exception("Requesting staff #{$row['requested_by_staff_id']} does not exist or is not active.");
                }
            }

            $entry = self::registry()[$command_type] ?? null;
            if (!$entry)
                throw new Exception("Unknown command type: {$command_type}");
            list($file, $class, $method, $event_type, $permission) = $entry;
            self::assertPermitted($command_type, $permission);
            require_once dirname(__FILE__) . "/handlers/{$file}.php";
            $result = $class::$method($payload, $row['requested_by_staff_id']);

            $this->assertCreateReturnedId($command_type, $result);

            // Mark inbox row as done
            db_query(sprintf(
                "UPDATE " . FASTAPI_INBOX_TABLE . " SET status='done', result_payload=%s, processed_at=NOW() WHERE id=%d",
                db_input(json_encode($result)),
                $inbox_id
            ));

            // Emit outbox event for Python worker
            $this->emitOutboxEvent(
                $event_type,
                $row['aggregate_type'],
                $row['aggregate_id'],
                $result
            );

            error_log("[FastAPI Integration] Command #{$inbox_id} ({$command_type}) completed.");

        } catch (Throwable $e) {
            // Throwable, not Exception: a handler calling a method osTicket doesn't have raises
            // an Error, which must fail this row instead of wedging it in 'processing'.
            error_log("[FastAPI Integration] Command #{$inbox_id} ({$command_type}) failed: " . $e->getMessage());
            db_query(sprintf(
                "UPDATE " . FASTAPI_INBOX_TABLE . " SET status='failed', error_message=%s, processed_at=NOW() WHERE id=%d",
                db_input($e->getMessage()),
                $inbox_id
            ));
        } finally {
            self::$runningCommand = false;
            $this->scrubPayload($inbox_id, $payload);
        }
    }

    /**
     * The requesting staff member must hold what osTicket's own page for this job requires (the registry's
     * permission column) — checked here, at the point that actually performs the write, so it holds even if
     * the API's check were bypassed or wrong. Fails closed: an unknown kind is a refusal.
     */
    private static function assertPermitted($command_type, $permission) {
        global $thisstaff;
        if (!$thisstaff)
            throw new Exception("Permission denied: {$command_type} needs a requesting staff member.");

        if ($permission === 'admin') {
            if ($thisstaff->isAdmin())
                return;
            $needs = 'administrator access';
        } else {
            $parts = explode(':', (string) $permission, 2);
            $kind = $parts[0];
            $key = $parts[1] ?? '';
            // global: the staff member's own permissions; role: any of their roles (Staff::hasPerm($key, false))
            if ($key !== '' && (($kind === 'global' && $thisstaff->hasPerm($key))
                    || ($kind === 'role' && $thisstaff->hasPerm($key, false))))
                return;
            $needs = "the '{$key}' permission";
        }
        throw new Exception("Permission denied: the requesting staff member lacks {$needs}, which {$command_type} requires.");
    }

    /** Keys whose values are secrets (a staff member's password); matches the API's own masking. */
    const SECRET_KEY = '/pass(word|wd)?|secret|token|file_data/i';

    private static function redactSecrets($value, &$changed) {
        if (!is_array($value))
            return $value;
        foreach ($value as $key => $item) {
            if (is_string($key) && preg_match(self::SECRET_KEY, $key) && $item !== '[redacted]' && $item !== null && $item !== '') {
                $value[$key] = '[redacted]';
                $changed = true;
            } elseif (is_array($item)) {
                $value[$key] = self::redactSecrets($item, $changed);
            }
        }
        return $value;
    }

    /**
     * Once a command has finished (done or failed) its secrets have served their purpose: overwrite them in
     * the stored payload so a staff member's password is not left in the inbox, where the commands API and
     * anyone with database access could read it. (A row still pending or held by a killed run keeps its payload
     * until it finishes.)
     */
    private function scrubPayload($inbox_id, $payload) {
        try {
            $changed = false;
            $clean = self::redactSecrets(is_array($payload) ? $payload : [], $changed);
            if ($changed) {
                db_query(sprintf(
                    "UPDATE " . FASTAPI_INBOX_TABLE . " SET payload=%s WHERE id=%d",
                    db_input(json_encode($clean)),
                    $inbox_id
                ));
            }
        } catch (Throwable $e) {
            error_log("[FastAPI Integration] could not scrub payload of command #{$inbox_id}: " . $e->getMessage());
        }
    }

    /**
     * A ".create" handler that reports success without an id did not persist anything
     * (osTicket's Model::create() only instantiates; the row exists once save()/update()
     * succeeds). Fail the command instead of reporting a phantom "done".
     */
    private function assertCreateReturnedId($command_type, $result) {
        if (substr($command_type, -7) !== '.create')
            return;
        foreach ((array) $result as $key => $value) {
            if (substr($key, -2) === 'id' && $value)
                return;
        }
        throw new Exception("Handler reported success but returned no id — nothing was created: " . json_encode($result));
    }

    /**
     * Write an event to the outbox for the Python worker to process.
     */
    private function emitOutboxEvent($event_type, $aggregate_type, $aggregate_id, $payload) {
        db_query(sprintf(
            "INSERT INTO " . FASTAPI_OUTBOX_TABLE . " (event_type, aggregate_type, aggregate_id, payload, status, created_at)
             VALUES (%s, %s, %s, %s, 'pending', NOW())",
            db_input($event_type),
            db_input($aggregate_type),
            db_input($aggregate_id),
            db_input(json_encode($payload))
        ));
    }

    // ── Outbound Event Listeners ───────────────────────────────────────────

    function onModelCreated($object, $data=null) {
        $this->handleModelEvent('created', $object);
    }

    function onModelUpdated($object, $data=null) {
        $this->handleModelEvent('updated', $object);
    }

    function onModelDeleted($object, $data=null) {
        $this->handleModelEvent('deleted', $object);
    }

    function onObjectCreated($object, $data=null) {
        $this->handleModelEvent('created', $object);
    }

    function onObjectEdited($object, $data=null) {
        $this->handleModelEvent('updated', $object);
    }

    function onObjectDeleted($object, $data=null) {
        $this->handleModelEvent('deleted', $object);
    }

    /**
     * True while a command runs: it emits its own result event, so the model signals it triggers would
     * duplicate it. STATIC on purpose: osTicket calls the plugin instance it bootstrapped, while the worker
     * runs commands on another instance — a per-instance flag was never seen by the signal listeners, and
     * every command produced three extra events.
     */
    private static $runningCommand = false;

    /** Signal events already written by this PHP process, so the same change is not emitted twice
     *  (osTicket fires both model.* and object.* for it). */
    private static $emittedSignals = [];

    private function handleModelEvent($action, $object) {
        if (!is_object($object) || self::$runningCommand) return;

        $class = get_class($object);
        $typeMap = [
            'Ticket' => 'ticket',
            'Task' => 'task',
            'User' => 'user',
            'Organization' => 'org',
            'Staff' => 'staff',
            'Team' => 'team',
            'Dept' => 'dept',
            'Category' => 'kb.category',
            'FAQ' => 'kb.faq',
            'Canned' => 'kb.canned',
            'DynamicForm' => 'form',
            'Banlist' => 'banlist'
        ];

        if (!isset($typeMap[$class])) {
            return;
        }

        $aggregate_type = $typeMap[$class];
        $event_type = "{$aggregate_type}.{$action}";
        
        // Extract aggregate ID (most ORM models use getId())
        $aggregate_id = method_exists($object, 'getId') ? $object->getId() : 0;
        if (!$aggregate_id && isset($object->id)) {
            $aggregate_id = $object->id;
        }

        if (!$aggregate_id) return;

        $payload = self::safePayload($object);
        $fingerprint = md5($event_type . '|' . $aggregate_id . '|' . json_encode($payload));
        if (isset(self::$emittedSignals[$fingerprint]))
            return;
        self::$emittedSignals[$fingerprint] = true;

        $this->emitOutboxEvent($event_type, $aggregate_type, $aggregate_id, $payload);
    }

    /** Column names never sent to the outbox (it is broadcast over WebSocket). */
    const SENSITIVE_KEY = '/pass|hash|secret|token|salt|key|signature|backend/i';

    /**
     * Scalar column values of a model, minus anything credential-like. Staff::$ht holds
     * `passwd`, so the raw row must never be the payload.
     */
    private static function safePayload($object) {
        $ht = (isset($object->ht) && is_array($object->ht)) ? $object->ht : [];
        $safe = [];
        foreach ($ht as $column => $value) {
            if (is_scalar($value) || $value === null) {
                if (!preg_match(self::SENSITIVE_KEY, (string) $column))
                    $safe[$column] = $value;
            }
        }
        return $safe;
    }

    /** command type => [handler file, class, method, outbox event] — see registry.php. */
    private static function registry() {
        static $registry = null;
        return $registry ?? ($registry = require dirname(__FILE__) . '/registry.php');
    }

    /**
     * Register the auth API endpoint.
     */
    function registerApiEndpoints($dispatcher) {
        error_log("FastApiIntegrationPlugin registerApiEndpoints CALLED! " . get_class($dispatcher));
        $dispatcher->append(
            url_post("^/auth/integration", array($this, 'authenticateForFastApi'))
        );
        $dispatcher->append(
            url_get("^/fast-ticket/handshake", array($this, 'handshake'))
        );
        $dispatcher->append(
            url_post("^/fast-ticket/read", array($this, 'bridgeRead'))
        );
    }

    /**
     * GET /api/fast-ticket/handshake — what fast-ticket's `make doctor` asks the instance.
     * Needs the integration key, like login. Read-only; reveals versions and readiness only.
     */
    function handshake() {
        header('Content-Type: application/json');
        $expected = self::integrationKey();
        $provided = $_SERVER['HTTP_X_INTEGRATION_KEY'] ?? '';
        if (!$expected || !hash_equals($expected, $provided)) {
            http_response_code(403);
            echo json_encode(['error' => 'Invalid integration key.']);
            return;
        }
        $tables_ready = true;
        foreach ([FASTAPI_INBOX_TABLE, FASTAPI_OUTBOX_TABLE] as $table) {
            $res = db_query('SHOW TABLES LIKE ' . db_input($table));
            if (!$res || !db_num_rows($res))
                $tables_ready = false;
        }
        $sig = db_query("SELECT value FROM " . CONFIG_TABLE . " WHERE namespace='core' AND `key`='schema_signature' LIMIT 1");
        $sig = $sig ? db_fetch_array($sig) : null;
        echo json_encode([
            'plugin_version'   => self::VERSION,
            'osticket_version' => defined('THIS_VERSION') ? THIS_VERSION : null,
            'schema_signature' => $sig ? $sig['value'] : null,
            'table_prefix'     => TABLE_PREFIX,
            'php_version'      => PHP_VERSION,
            'tables_ready'     => $tables_ready,
            'worker_available' => function_exists('pcntl_exec'),
            // the read endpoint (read.php, reads.php): canned-response variables and files in any storage backend
            'read_available'   => is_file(dirname(__FILE__) . '/read.php') && is_file(dirname(__FILE__) . '/reads.php'),
        ]);
    }

    /** POST /api/fast-ticket/read — synchronous, read-only questions for osTicket's own PHP (see read.php). */
    function bridgeRead() {
        require_once dirname(__FILE__) . '/read.php';
    }

    function authenticateForFastApi() {
        require_once dirname(__FILE__) . '/auth.php';
    }
}

// Plugin registration
return array(
    'id'          => 'fastapi_integration',
    'version'     => FastApiIntegrationPlugin::VERSION,
    'name'        => 'FastAPI Integration Bridge',
    'author'      => 'fast-ticket',
    'description' => 'Bridges fast-ticket FastAPI requests with osTicket PHP model layer.',
    'url'         => 'https://github.com/coreops/fast-ticket',
    'plugin'      => 'plugin.php:FastApiIntegrationPlugin',
);
