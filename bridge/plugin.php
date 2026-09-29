<?php
/**
 * FastAPI Integration Plugin for osTicket
 *
 * Bridges the fast-ticket FastAPI layer with osTicket's PHP model layer.
 * Consumes ost_integration_inbox (commands from FastAPI) and produces
 * ost_integration_outbox (events for the Python outbox worker).
 *
 * Architecture:
 *   FastAPI → ost_integration_inbox → [this plugin via cron] → osTicket PHP models
 *   osTicket PHP models → ost_integration_outbox → Python outbox worker → WebSocket
 *
 * The plugin hooks into osTicket's Signal system ('cron') to drain the inbox
 * every minute via api/cron.php.
 */

require_once INCLUDE_DIR . 'class.plugin.php';

/**
 * osTicket only bootstraps a plugin through an enabled instance whose config
 * object resolves (PluginInstance::bootstrap), so even a plugin with no
 * settings needs a PluginConfig subclass. The integration key itself lives in
 * the container env / ost_config — see FastApiIntegrationPlugin::integrationKey().
 */
class FastApiIntegrationPluginConfig extends PluginConfig {
    function getOptions() {
        return array();
    }
}

class FastApiIntegrationPlugin extends Plugin {

    var $config_class = 'FastApiIntegrationPluginConfig';

    /** Integration key from env (compose) or the ost_config row install.php writes. */
    static function integrationKey() {
        $key = getenv('FASTAPI_INTEGRATION_KEY') ?: ($_SERVER['FASTAPI_INTEGRATION_KEY'] ?? '');
        if ($key)
            return $key;
        $res = db_query("SELECT value FROM ost_config WHERE namespace='fastapi_integration' AND `key`='integration_key' LIMIT 1");
        $row = $res ? db_fetch_array($res) : null;
        return $row ? $row['value'] : '';
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
        // The plugin defines no PluginConfig class, so getConfig() is null. The key
        // is provisioned by install.php (ost_config) and mirrored in the container env.
        if (!self::integrationKey()) {
            error_log('[FastAPI Integration] Plugin not configured — skipping inbox drain.');
            return;
        }

        $batch_size = 50;

        // ── Stuck-row recovery ───────────────────────────────────────────────
        // A row is 'processing' only while a run holds it, and processed_at doubles as
        // the claim time until the row finishes (see the claim below). One held for over
        // 5 minutes belongs to a run that died (fatal, OOM, killed). Fail it rather than
        // retry: it may have partly executed, and re-running a non-idempotent command
        // (ticket.create, ...) would duplicate work — and a handler that always crashes
        // would otherwise be retried forever.
        db_query("UPDATE ost_integration_inbox
                   SET status='failed', processed_at=NOW(),
                       error_message='Timed out while processing (the handler crashed or the run was killed); not retried automatically.'
                   WHERE status='processing'
                     AND COALESCE(processed_at, created_at) < DATE_SUB(NOW(), INTERVAL 5 MINUTE)");

        // ── Atomic claim ─────────────────────────────────────────────────────
        // Generate a unique run ID for this invocation so concurrent cron runs
        // can't steal each other's rows.
        $run_id = db_input(bin2hex(random_bytes(8)));

        db_query("UPDATE ost_integration_inbox
                   SET status='processing', claimed_by={$run_id}, processed_at=NOW()
                   WHERE status='pending'
                   ORDER BY id ASC
                   LIMIT {$batch_size}");

        // Only fetch rows we own (identified by our run_id).
        $rows = db_query("SELECT * FROM ost_integration_inbox
                          WHERE status='processing' AND claimed_by={$run_id}
                          ORDER BY id ASC");

        while ($row = db_fetch_array($rows)) {
            $this->dispatchCommand($row);
        }
    }

    /**
     * Route a single inbox command to the appropriate handler.
     */
    private function dispatchCommand($row) {
        $payload = json_decode($row['payload'], true) ?: [];
        $command_type = $row['command_type'];
        $inbox_id = $row['id'];

        error_log("[FastAPI Integration] Processing command #{$inbox_id}: {$command_type}");

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

            switch ($command_type) {
                // ── Tickets ────────────────────────────────────────────────
                case 'ticket.create':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.reply':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::reply($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.note':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::addNote($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.close':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::close($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.reopen':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::reopen($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.assign':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::assign($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.assign_team':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::assignTeam($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.transfer':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::transfer($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.change_owner':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::changeOwner($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.delete':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::delete($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.merge':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::merge($payload, $row['requested_by_staff_id']);
                    break;
                case 'ticket.answered':
                case 'ticket.unanswered':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::setAnswered($payload, $command_type === 'ticket.answered', $row['requested_by_staff_id']);
                    break;
                case 'ticket.collaborator.add':
                    require_once dirname(__FILE__) . '/handlers/tickets.php';
                    $result = FastApiTicketHandler::addCollaborator($payload, $row['requested_by_staff_id']);
                    break;

                // ── Tasks ──────────────────────────────────────────────────
                case 'task.create':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.assign':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::assign($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.close':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::close($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.delete':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::delete($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.reopen':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::reopen($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.note':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::addNote($payload, $row['requested_by_staff_id']);
                    break;
                case 'task.edit':
                    require_once dirname(__FILE__) . '/handlers/tasks.php';
                    $result = FastApiTaskHandler::edit($payload, $row['requested_by_staff_id']);
                    break;

                // ── Users ──────────────────────────────────────────────────
                case 'user.create':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'user.update':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'user.delete':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Organizations ──────────────────────────────────────────
                case 'org.create':
                    require_once dirname(__FILE__) . '/handlers/orgs.php';
                    $result = FastApiOrgHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'org.update':
                    require_once dirname(__FILE__) . '/handlers/orgs.php';
                    $result = FastApiOrgHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'org.delete':
                    require_once dirname(__FILE__) . '/handlers/orgs.php';
                    $result = FastApiOrgHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Knowledgebase ──────────────────────────────────────────
                case 'kb.category.create':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::createCategory($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.category.update':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::updateCategory($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.category.delete':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::deleteCategory($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.faq.create':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::createFaq($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.faq.update':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::updateFaq($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.faq.delete':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::deleteFaq($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.canned.create':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::createCanned($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.canned.update':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::updateCanned($payload, $row['requested_by_staff_id']);
                    break;
                case 'kb.canned.delete':
                    require_once dirname(__FILE__) . '/handlers/kb.php';
                    $result = FastApiKbHandler::deleteCanned($payload, $row['requested_by_staff_id']);
                    break;

                // ── Staff ──────────────────────────────────────────────────
                case 'staff.create':
                    require_once dirname(__FILE__) . '/handlers/staff.php';
                    $result = FastApiStaffHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'staff.update':
                    require_once dirname(__FILE__) . '/handlers/staff.php';
                    $result = FastApiStaffHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'staff.delete':
                    require_once dirname(__FILE__) . '/handlers/staff.php';
                    $result = FastApiStaffHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Departments ────────────────────────────────────────────
                case 'dept.create':
                    require_once dirname(__FILE__) . '/handlers/departments.php';
                    $result = FastApiDepartmentHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'dept.update':
                    require_once dirname(__FILE__) . '/handlers/departments.php';
                    $result = FastApiDepartmentHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'dept.delete':
                    require_once dirname(__FILE__) . '/handlers/departments.php';
                    $result = FastApiDepartmentHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Teams ──────────────────────────────────────────────────
                case 'team.create':
                    require_once dirname(__FILE__) . '/handlers/teams.php';
                    $result = FastApiTeamHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'team.update':
                    require_once dirname(__FILE__) . '/handlers/teams.php';
                    $result = FastApiTeamHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'team.delete':
                    require_once dirname(__FILE__) . '/handlers/teams.php';
                    $result = FastApiTeamHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Help Topics ────────────────────────────────────────────
                case 'topic.create':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiTopicHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'topic.update':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiTopicHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'topic.delete':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiTopicHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── SLAs ───────────────────────────────────────────────────
                case 'sla.create':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiSlaHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'sla.update':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiSlaHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'sla.delete':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiSlaHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Priorities ─────────────────────────────────────────────
                case 'priority.create':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiPriorityHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'priority.update':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiPriorityHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'priority.delete':
                    require_once dirname(__FILE__) . '/handlers/lookups.php';
                    $result = FastApiPriorityHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Dynamic Forms ──────────────────────────────────────────
                case 'form.create':
                    require_once dirname(__FILE__) . '/handlers/forms.php';
                    $result = FastApiFormHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'form.update':
                    require_once dirname(__FILE__) . '/handlers/forms.php';
                    $result = FastApiFormHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'form.delete':
                    require_once dirname(__FILE__) . '/handlers/forms.php';
                    $result = FastApiFormHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                // ── Banlist ────────────────────────────────────────────────
                case 'banlist.create':
                    require_once dirname(__FILE__) . '/handlers/banlist.php';
                    $result = FastApiBanlistHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'banlist.update':
                    require_once dirname(__FILE__) . '/handlers/banlist.php';
                    $result = FastApiBanlistHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'banlist.delete':
                    require_once dirname(__FILE__) . '/handlers/banlist.php';
                    $result = FastApiBanlistHandler::delete($payload, $row['requested_by_staff_id']);
                    break;

                default:
                    throw new Exception("Unknown command type: {$command_type}");
            }

            $this->assertCreateReturnedId($command_type, $result);

            // Mark inbox row as done
            db_query(sprintf(
                "UPDATE ost_integration_inbox SET status='done', result_payload=%s, processed_at=NOW() WHERE id=%d",
                db_input(json_encode($result)),
                $inbox_id
            ));

            // Emit outbox event for Python worker
            $this->emitOutboxEvent(
                $this->commandToEventType($command_type),
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
                "UPDATE ost_integration_inbox SET status='failed', error_message=%s, processed_at=NOW() WHERE id=%d",
                db_input($e->getMessage()),
                $inbox_id
            ));
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
            "INSERT INTO ost_integration_outbox (event_type, aggregate_type, aggregate_id, payload, status, created_at)
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

    private function handleModelEvent($action, $object) {
        if (!is_object($object)) return;
        
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

        // Serialize full model state for payload
        $payload = method_exists($object, 'ht') ? $object->ht : (array)$object;
        
        $this->emitOutboxEvent($event_type, $aggregate_type, $aggregate_id, $payload);
    }

    /**
     * Map a command type to its corresponding outbox event type.
     */
    private function commandToEventType($command_type) {
        $map = [
            'ticket.create'           => 'ticket.created',
            'ticket.reply'            => 'ticket.replied',
            'ticket.note'             => 'ticket.note_added',
            'ticket.close'            => 'ticket.closed',
            'ticket.reopen'           => 'ticket.reopened',
            'ticket.assign'           => 'ticket.assigned',
            'ticket.assign_team'      => 'ticket.team_assigned',
            'ticket.transfer'         => 'ticket.transferred',
            'ticket.change_owner'     => 'ticket.owner_changed',
            'ticket.delete'           => 'ticket.deleted',
            'ticket.merge'            => 'ticket.merged',
            'ticket.answered'         => 'ticket.answered',
            'ticket.unanswered'       => 'ticket.unanswered',
            'ticket.collaborator.add' => 'ticket.collaborator_added',
            'task.create'             => 'task.created',
            'task.assign'             => 'task.assigned',
            'task.close'              => 'task.closed',
            'task.delete'             => 'task.deleted',
            'task.reopen'             => 'task.reopened',
            'task.note'               => 'task.note_added',
            'task.edit'               => 'task.edited',
            'user.create'             => 'user.created',
            'user.update'             => 'user.updated',
            'user.delete'             => 'user.deleted',
            'staff.create'            => 'staff.created',
            'staff.update'            => 'staff.updated',
            'staff.delete'            => 'staff.deleted',
            'dept.create'             => 'dept.created',
            'dept.update'             => 'dept.updated',
            'dept.delete'             => 'dept.deleted',
            'team.create'             => 'team.created',
            'team.update'             => 'team.updated',
            'team.delete'             => 'team.deleted',
            'topic.create'            => 'topic.created',
            'topic.update'            => 'topic.updated',
            'topic.delete'            => 'topic.deleted',
            'sla.create'              => 'sla.created',
            'sla.update'              => 'sla.updated',
            'sla.delete'              => 'sla.deleted',
            'priority.create'         => 'priority.created',
            'priority.update'         => 'priority.updated',
            'priority.delete'         => 'priority.deleted',
            'form.create'             => 'form.created',
            'form.update'             => 'form.updated',
            'form.delete'             => 'form.deleted',
            'banlist.create'          => 'banlist.created',
            'banlist.update'          => 'banlist.updated',
            'banlist.delete'          => 'banlist.deleted',
            'org.create'              => 'org.created',
            'org.update'              => 'org.updated',
            'org.delete'              => 'org.deleted',
            'kb.category.create'      => 'kb.category.created',
            'kb.category.update'      => 'kb.category.updated',
            'kb.category.delete'      => 'kb.category.deleted',
            'kb.faq.create'           => 'kb.faq.created',
            'kb.faq.update'           => 'kb.faq.updated',
            'kb.faq.delete'           => 'kb.faq.deleted',
            'kb.canned.create'        => 'kb.canned.created',
            'kb.canned.update'        => 'kb.canned.updated',
            'kb.canned.delete'        => 'kb.canned.deleted',
        ];
        return $map[$command_type] ?? ($command_type . '.executed');
    }

    /**
     * Register the auth API endpoint.
     */
    function registerApiEndpoints($dispatcher) {
        error_log("FastApiIntegrationPlugin registerApiEndpoints CALLED! " . get_class($dispatcher));
        $dispatcher->append(
            url_post("^/auth/integration", array($this, 'authenticateForFastApi'))
        );
    }

    function authenticateForFastApi() {
        require_once dirname(__FILE__) . '/auth.php';
    }
}

// Plugin registration
return array(
    'id'          => 'fastapi_integration',
    'version'     => '1.0',
    'name'        => 'FastAPI Integration Bridge',
    'author'      => 'fast-ticket',
    'description' => 'Bridges fast-ticket FastAPI requests with osTicket PHP model layer.',
    'url'         => 'https://github.com/coreops/fast-ticket',
    'plugin'      => 'plugin.php:FastApiIntegrationPlugin',
);
