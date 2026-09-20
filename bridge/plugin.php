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

class FastApiIntegrationPlugin extends Plugin {


    function __construct($id=0) {
        parent::__construct($id);
        error_log("FastApiIntegrationPlugin __construct CALLED!");
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

    function bootstrap() {
        // Nothing here. Signals moved to constructor.
    }

    /**
     * Drain the integration inbox — called every minute by cron.
     *
     * Claims rows atomically (status = pending → processing), dispatches to
     * the appropriate handler, then writes a result event to the outbox.
     */
    function processInbox() {
        $config = $this->getConfig();
        if (!$config->get('integration_key')) {
            error_log('[FastAPI Integration] Plugin not configured — skipping inbox drain.');
            return;
        }

        $db = db_connect();
        $batch_size = 50;
        $now = date('Y-m-d H:i:s');

        // Claim a batch of pending commands atomically
        $claim_sql = "UPDATE ost_integration_inbox
                      SET status = 'processing', processed_at = NULL
                      WHERE status = 'pending'
                      ORDER BY id ASC
                      LIMIT {$batch_size}";
        db_query($claim_sql);

        // Fetch the batch we just claimed (identified by status=processing)
        $rows = db_query("SELECT * FROM ost_integration_inbox WHERE status = 'processing' ORDER BY id ASC LIMIT {$batch_size}");

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

                // ── Users ──────────────────────────────────────────────────
                case 'user.create':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::create($payload, $row['requested_by_staff_id']);
                    break;
                case 'user.update':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::update($payload, $row['requested_by_staff_id']);
                    break;
                case 'user.set_org':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::setOrg($payload, $row['requested_by_staff_id']);
                    break;
                case 'user.delete':
                    require_once dirname(__FILE__) . '/handlers/users.php';
                    $result = FastApiUserHandler::delete($payload, $row['requested_by_staff_id']);
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

                default:
                    throw new Exception("Unknown command type: {$command_type}");
            }

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

        } catch (Exception $e) {
            error_log("[FastAPI Integration] Command #{$inbox_id} ({$command_type}) failed: " . $e->getMessage());
            db_query(sprintf(
                "UPDATE ost_integration_inbox SET status='failed', error_message=%s, processed_at=NOW() WHERE id=%d",
                db_input($e->getMessage()),
                $inbox_id
            ));
        }
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
            'user.create'             => 'user.created',
            'user.update'             => 'user.updated',
            'user.set_org'            => 'user.org_set',
            'user.delete'             => 'user.deleted',
            'staff.create'            => 'staff.created',
            'staff.update'            => 'staff.updated',
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
