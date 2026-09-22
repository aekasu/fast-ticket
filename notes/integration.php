<?php

require_once(INCLUDE_DIR . 'class.plugin.php');
require_once(INCLUDE_DIR . 'class.signal.php');

class FastApiIntegrationPlugin extends Plugin {
    var $config_class = 'FastApiIntegrationConfig';

    // -----------------------------------------------------------------------
    // Schema migrations: any column additions go here. Called every bootstrap.
    // -----------------------------------------------------------------------
    private function migrateSchema() {
        $table = TABLE_PREFIX . 'integration_outbox';
        db_query("ALTER TABLE `{$table}`
            ADD COLUMN IF NOT EXISTS `idempotency_key` varchar(255) DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS `locked_at` datetime DEFAULT NULL", false);
        db_query("ALTER TABLE `{$table}` ADD UNIQUE KEY IF NOT EXISTS `ux_idempotency_key` (`idempotency_key`)", false);

        $table = TABLE_PREFIX . 'integration_inbox';
        db_query("ALTER TABLE `{$table}`
            ADD COLUMN IF NOT EXISTS `idempotency_key` varchar(255) DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS `requested_by_staff_id` int(11) unsigned DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS `locked_at` datetime DEFAULT NULL", false);
        db_query("ALTER TABLE `{$table}` ADD UNIQUE KEY IF NOT EXISTS `ux_idempotency_key` (`idempotency_key`)", false);
    }

    // -----------------------------------------------------------------------
    // Bootstrap: create tables, run migrations, connect signals
    // -----------------------------------------------------------------------
    function bootstrap() {
        // Outbox: events flowing osTicket -> FastAPI
        $sql = "CREATE TABLE IF NOT EXISTS `" . TABLE_PREFIX . "integration_outbox` (
            `id`              int(11) unsigned NOT NULL AUTO_INCREMENT,
            `idempotency_key` varchar(255)     DEFAULT NULL,
            `event_type`      varchar(255)     NOT NULL,
            `schema_version`  int(11)          NOT NULL DEFAULT 1,
            `aggregate_type`  varchar(255)     NOT NULL,
            `aggregate_id`    varchar(255)     NOT NULL,
            `payload`         text             NOT NULL,
            `status`          varchar(50)      NOT NULL DEFAULT 'pending',
            `retry_count`     int(11)          NOT NULL DEFAULT 0,
            `error_message`   text             DEFAULT NULL,
            `created_at`      datetime         NOT NULL,
            `processed_at`    datetime         DEFAULT NULL,
            `locked_at`       datetime         DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `ux_idempotency_key` (`idempotency_key`),
            KEY `idx_status_created` (`status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;";
        db_query($sql);

        // Inbox: commands flowing FastAPI -> osTicket
        $sql = "CREATE TABLE IF NOT EXISTS `" . TABLE_PREFIX . "integration_inbox` (
            `id`            int(11) unsigned NOT NULL AUTO_INCREMENT,
            `idempotency_key` varchar(255)     DEFAULT NULL,
            `command_type`  varchar(255)     NOT NULL,
            `aggregate_type` varchar(255)    NOT NULL,
            `aggregate_id`  varchar(255)     NOT NULL,
            `payload`       text             NOT NULL,
            `status`        varchar(50)      NOT NULL DEFAULT 'pending',
            `retry_count`   int(11)          NOT NULL DEFAULT 0,
            `error_message` text             DEFAULT NULL,
            `created_at`    datetime         NOT NULL,
            `processed_at`  datetime         DEFAULT NULL,
            `locked_at`     datetime         DEFAULT NULL,
            `requested_by_staff_id` int(11) unsigned DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `ux_idempotency_key` (`idempotency_key`),
            KEY `idx_status_created` (`status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;";
        db_query($sql);

        // Dead-letter table: permanently failed events for human review
        $sql = "CREATE TABLE IF NOT EXISTS `" . TABLE_PREFIX . "integration_dead_letter` (
            `id`            int(11) unsigned NOT NULL AUTO_INCREMENT,
            `source_table`  varchar(50)      NOT NULL COMMENT 'outbox or inbox',
            `source_id`     int(11) unsigned NOT NULL,
            `event_type`    varchar(255)     NOT NULL,
            `payload`       text             NOT NULL,
            `error_message` text             DEFAULT NULL,
            `created_at`    datetime         NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;";
        db_query($sql);

        $this->migrateSchema();

        // ----- Signal connections -------------------------------------------
        Signal::connect('ticket.created',     array($this, 'onTicketCreated'));
        Signal::connect('object.edited',      array($this, 'onObjectEdited'), 'Ticket');
        Signal::connect('object.deleted',     array($this, 'onObjectDeleted'), 'Ticket');
        Signal::connect('threadentry.created',array($this, 'onThreadEntryCreated'));
        Signal::connect('user.created',       array($this, 'onUserCreated'));
        Signal::connect('organization.created', array($this, 'onOrganizationCreated'));
        Signal::connect('task.created',       array($this, 'onTaskCreated'));
        Signal::connect('cron',               array($this, 'processInbox'));
        Signal::connect('api',                array($this, 'registerApiRoutes'));
    }

    // -----------------------------------------------------------------------
    // FastAPI authentication endpoint
    // -----------------------------------------------------------------------
    function registerApiRoutes($dispatcher) {
        $dispatcher->append(
            url_post("^/auth/integration$", array($this, 'authenticateForFastApi'))
        );
    }

    function authenticateForFastApi() {
        // Keep the shared secret in the process environment rather than the
        // osTicket plugin configuration table. It is deployment secret material,
        // not application configuration.
        $expectedKey = (string) getenv('FASTAPI_INTEGRATION_KEY');
        $providedKey = isset($_SERVER['HTTP_X_FASTAPI_INTEGRATION_KEY'])
            ? (string) $_SERVER['HTTP_X_FASTAPI_INTEGRATION_KEY']
            : '';

        if (!$expectedKey || !$providedKey || !hash_equals($expectedKey, $providedKey)) {
            Http::response(403, json_encode(array(
                'detail' => 'Invalid integration credentials'
            )), 'application/json');
        }

        $username = isset($_POST['username']) ? trim((string) $_POST['username']) : '';
        $password = isset($_POST['password']) ? (string) $_POST['password'] : '';

        if ($username === '' || $password === '') {
            Http::response(400, json_encode(array(
                'detail' => 'Username and password are required'
            )), 'application/json');
        }

        try {
            // Use osTicket's own local authentication backend. FastAPI never
            // reads or verifies the stored password hash itself.
            $backend = StaffAuthenticationBackend::getBackend('local');
            if (!$backend) {
                $this->logError('Local staff authentication backend is unavailable');
                Http::response(500, json_encode(array(
                    'detail' => 'Authentication backend unavailable'
                )), 'application/json');
            }

            $staff = $backend->authenticate($username, $password);

            if (!$staff || !($staff instanceof Staff) || !$staff->getId()
                    || !$staff->isActive()) {
                Http::response(401, json_encode(array(
                    'detail' => 'Incorrect username or password'
                )), 'application/json');
            }

            Http::response(200, json_encode(array(
                'staff_id'  => (int) $staff->getId(),
                'username'  => (string) $staff->getUserName(),
                'firstname' => (string) $staff->getFirstName(),
                'lastname'  => (string) $staff->getLastName(),
                'email'     => (string) $staff->getEmail(),
            )), 'application/json');
        } catch (Throwable $e) {
            $this->logError('Authentication endpoint failed: ' . $e->getMessage());
            Http::response(500, json_encode(array(
                'detail' => 'Authentication service failed'
            )), 'application/json');
        }
    }

    // -----------------------------------------------------------------------
    // Core outbox writer — all events flow through here
    // -----------------------------------------------------------------------
    private function writeOutbox($eventType, $aggregateType, $aggregateId, $payload, $idempotencyKey = null) {
        $encodedPayload = json_encode($payload);
        if ($encodedPayload === false) {
            $this->logError("Failed to JSON-encode payload for event: $eventType");
            return false;
        }

        $key = $idempotencyKey
            ? db_input($idempotencyKey, false)
            : db_input($eventType . ':' . $aggregateId . ':' . uniqid('', true), false);

        $sql = "INSERT INTO `" . TABLE_PREFIX . "integration_outbox`
                    (idempotency_key, event_type, aggregate_type, aggregate_id, payload, status, created_at)
                VALUES (
                    '$key',
                    '" . db_input($eventType, false) . "',
                    '" . db_input($aggregateType, false) . "',
                    '" . db_input($aggregateId, false) . "',
                    '" . db_input($encodedPayload, false) . "',
                    'pending',
                    NOW()
                )
                ON DUPLICATE KEY UPDATE id=id"; // Silently ignore true duplicates (idempotency)

        $result = db_query($sql, false); // false = don't die on error
        if (!$result) {
            // Outbox write failed — log the loss so it can be investigated
            // but NEVER let this bubble up and disrupt the osTicket request.
            $this->logError("Outbox write failed for event $eventType/$aggregateId: " . db_error());
            return false;
        }
        return true;
    }

    // -----------------------------------------------------------------------
    // Inbox processor — called by cron (not on request thread)
    // -----------------------------------------------------------------------
    function processInbox() {
        $sql = "SELECT * FROM `" . TABLE_PREFIX . "integration_inbox`
                WHERE status IN ('pending', 'retrying')
                  AND (locked_at IS NULL OR locked_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE))
                ORDER BY created_at ASC
                LIMIT 50";
        $result = db_query($sql, false);
        if (!$result) {
            $this->logError('Inbox query failed: ' . db_error());
            return;
        }

        while ($row = db_fetch_array($result)) {
            // Claim the row atomically. This prevents overlapping cron jobs from
            // executing the same command concurrently.
            $claimed = db_query("UPDATE `" . TABLE_PREFIX . "integration_inbox`
                SET status='processing', locked_at=NOW()
                WHERE id=" . (int)$row['id'] . "
                  AND status IN ('pending', 'retrying')
                  AND (locked_at IS NULL OR locked_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE))", false);
            if (!$claimed || db_affected_rows() !== 1)
                continue;

            $payload = json_decode($row['payload'], true);
            $success = false;
            $errorMsg = '';

            if (!is_array($payload)) {
                $this->deadLetter('inbox', $row, 'Invalid JSON command payload');
                $this->updateInboxStatus($row['id'], 'failed', 'Invalid JSON command payload');
                continue;
            }

            $previousStaff = null;
            try {
                $previousStaff = $this->setActorContext($row['requested_by_staff_id'] ?? null);
                switch ($row['command_type']) {
                    case 'ticket.create':
                        $success = $this->executeCreateTicket($payload);
                        break;
                    case 'ticket.reply':
                        $success = $this->executeReplyTicket($payload);
                        break;
                    case 'ticket.assign':
                        $success = $this->executeAssignTicket($payload);
                        break;
                    case 'ticket.close':
                        $success = $this->executeCloseTicket($payload);
                        break;
                    case 'ticket.reopen':
                        $success = $this->executeReopenTicket($payload);
                        break;
                    case 'ticket.answered':
                        $success = $this->executeMarkAnswered($payload);
                        break;
                    case 'ticket.unanswered':
                        $success = $this->executeMarkUnanswered($payload);
                        break;
                    case 'ticket.note':
                        $success = $this->executeAddNote($payload);
                        break;
                    case 'task.create':
                        $success = $this->executeCreateTask($payload);
                        break;
                    case 'task.assign':
                        $success = $this->executeAssignTask($payload);
                        break;
                    case 'task.close':
                        $success = $this->executeCloseTask($payload);
                        break;
                    case 'task.reopen':
                        $success = $this->executeReopenTask($payload);
                        break;
                    case 'task.note':
                        $success = $this->executeAddTaskNote($payload);
                        break;
                    case 'ticket.collaborator.add':
                        $success = $this->executeAddCollaborator($payload);
                        break;
                    case 'ticket.transfer':
                        $success = $this->executeTransferTicket($payload);
                        break;
                    case 'ticket.change_owner':
                        $success = $this->executeChangeOwner($payload);
                        break;
                    case 'ticket.delete':
                        $success = $this->executeDeleteTicket($payload);
                        break;
                    case 'ticket.update':
                        $success = $this->executeUpdateTicket($payload);
                        break;
                    case 'user.set_org':
                        $success = $this->executeSetUserOrg($payload);
                        break;
                    case 'user.create':
                        $success = $this->executeCreateUser($payload);
                        break;
                    case 'user.update':
                        $success = $this->executeUpdateUser($payload);
                        break;
                    default:
                        $this->deadLetter('inbox', $row, 'Unknown command type: ' . $row['command_type']);
                        $this->updateInboxStatus($row['id'], 'failed', 'Unknown command type');
                        continue 2;
                }
            } catch (Throwable $e) {
                $success = false;
                $errorMsg = $e->getMessage();
            } finally {
                $this->restoreActorContext($previousStaff);
            }

            if ($success) {
                $this->updateInboxStatus($row['id'], 'processed', null);
                continue;
            }

            $retries = (int)$row['retry_count'] + 1;
            $errorMsg = $errorMsg ?: db_error() ?: 'Command execution failed';
            if ($retries >= 3) {
                $this->deadLetter('inbox', $row, $errorMsg);
                $this->updateInboxStatus($row['id'], 'failed', $errorMsg, $retries);
            } else {
                $this->updateInboxStatus($row['id'], 'retrying', $errorMsg, $retries);
            }
        }
    }

    private function setActorContext($staffId) {
        global $thisstaff;
        $previous = isset($thisstaff) ? $thisstaff : null;
        if (!$staffId) {
            throw new Exception('Command is missing requested_by_staff_id');
        }
        require_once(INCLUDE_DIR . 'class.staff.php');
        $staff = Staff::lookup((int)$staffId);
        if (!$staff || !$staff->isActive()) {
            throw new Exception('Requesting staff member is not active');
        }
        $thisstaff = $staff;
        return $previous;
    }

    private function restoreActorContext($previous) {
        global $thisstaff;
        $thisstaff = $previous;
    }

    // -----------------------------------------------------------------------
    // Command executors — use native osTicket models only
    // -----------------------------------------------------------------------
    private function executeCreateTicket($payload) {
        // Delegate to osTicket's own Ticket model so all business logic applies
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $errors = array();
        $vars = array(
            'name'     => $payload['name'] ?? 'API User',
            'email'    => $payload['email'] ?? '',
            'subject'  => $payload['subject'] ?? '',
            'message'  => $payload['message'] ?? '',
            'source'   => 'API',
            'uid'      => null,
        );
        if (!empty($payload['dept_id'])) {
            $vars['deptId'] = (int)$payload['dept_id'];
        }
        if (!empty($payload['topic_id'])) {
            $vars['topicId'] = (int)$payload['topic_id'];
        }
        $ticket = Ticket::create($vars, $errors, $vars['source']);
        if (!$ticket) {
            throw new Exception('Ticket::create failed: ' . implode(', ', $errors));
        }
        return true;
    }

    private function executeReplyTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found: ' . ($payload['ticket_id'] ?? 'null'));
        $errors = array();
        // ResponseThreadEntry::add() validates against 'response', not
        // 'message' — postReply() passes $vars straight through unmapped.
        $ticket->postReply(array(
            'response' => $payload['message'] ?? '',
        ), $errors, !isset($payload['alert_customer']) || (bool)$payload['alert_customer']);
        if ($errors) throw new Exception(implode(', ', $errors));
        return true;
    }

    private function executeAssignTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.staff.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        $staff = Staff::lookup($payload['staff_id'] ?? 0);
        if (!$staff) throw new Exception('Staff not found');
        $note = $payload['note'] ?? 'Assigned via FastAPI integration';
        return (bool) $ticket->assignToStaff($staff, $note, false);
    }

    private function executeCloseTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.list.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        // Ticket has no close() method — closing is a status transition.
        // Look up whichever status is flagged as the "closed" state rather
        // than hardcoding a status name/ID, since those are admin-configurable.
        $status = TicketStatus::objects()->filter(array('state' => 'closed'))->first();
        if (!$status) throw new Exception('No status configured with state=closed');
        $errors = array();
        $ok = $ticket->setStatus($status, '', $errors, true, false);
        if (!$ok) throw new Exception($errors ? implode(', ', $errors) : 'setStatus failed');
        return true;
    }

    private function executeReopenTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        $status = TicketStatus::objects()->filter(array('state' => 'open'))->first();
        if (!$status) throw new Exception('No status configured with state=open');
        $errors = array();
        $ok = $ticket->setStatus($status, '', $errors, true, false);
        if (!$ok) throw new Exception($errors ? implode(', ', $errors) : 'setStatus failed');
        return true;
    }

    private function executeMarkAnswered($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        return (bool) $ticket->markAnswered();
    }

    private function executeMarkUnanswered($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        return (bool) $ticket->markUnAnswered();
    }

    // Posts an internal (staff-only) note on a ticket — used by the AI
    // triage loop to attach a summary/priority suggestion without touching
    // the ticket's dynamic form fields.
    private function executeAddNote($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found: ' . ($payload['ticket_id'] ?? 'null'));
        $title = $payload['title'] ?? 'System Note';
        $note  = $payload['message'] ?? '';
        if (!$note) throw new Exception('Note message is empty');
        // $alert=false — this is an automated note, don't email staff over it
        return (bool) $ticket->logNote($title, $note, 'SYSTEM', false);
    }

    private function executeCreateTask($payload) {
        global $thisstaff;
        require_once(INCLUDE_DIR . 'class.task.php');
        if (!$thisstaff)
            throw new Exception('No authenticated staff context for task creation');

        $ticketId = !empty($payload['ticket_id']) ? (int)$payload['ticket_id'] : 0;
        $deptId = 0;
        if ($ticketId) {
            require_once(INCLUDE_DIR . 'class.ticket.php');
            $ticket = Ticket::lookup($ticketId);
            if (!$ticket)
                throw new Exception('Ticket not found for task creation');
            $deptId = (int)$ticket->getDeptId();
        }
        if (!$deptId)
            $deptId = (int)$thisstaff->getDeptId();
        if (!$deptId)
            throw new Exception('Unable to determine task department');

        $vars = array(
            'object_id' => $ticketId,
            'object_type' => $ticketId ? 'T' : '',
            'internal_formdata' => array(
                'dept_id' => $deptId,
                'duedate' => null,
                'assignee' => null,
            ),
            'default_formdata' => array(
                'title' => $payload['title'] ?? 'API Task',
            ),
            'description' => $payload['description'] ?? '',
        );

        $task = Task::create($vars);
        if (!$task) {
            throw new Exception('Task::create failed (check staff ID 1 permissions and dept ID 1)');
        }
        return true;
    }

    // -----------------------------------------------------------------------
    private function executeAddCollaborator($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.user.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        $user = clone User::lookup($payload['user_id'] ?? 0);
        if (!$user) throw new Exception('User not found');
        $errors = array();
        // The addCollaborator method usually takes ($user, $vars, &$errors)
        if (!$ticket->addCollaborator($user, array('isactive' => 1), $errors)) {
            throw new Exception($errors ? implode(', ', $errors) : 'addCollaborator failed');
        }
        return true;
    }

    private function executeTransferTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.dept.php');
        require_once(INCLUDE_DIR . 'class.forms.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');

        $deptId = (int)($payload['dept_id'] ?? 0);
        $dept = Dept::lookup($deptId);
        if (!$dept) throw new Exception('Target Department not found');

        $form = $ticket->getTransferForm(array(
            'dept' => array($deptId),
            'refer' => false,
            'comments' => $payload['note'] ?? 'Transferred via FastAPI integration',
        ));
        $errors = array();
        if (!$ticket->transfer($form, $errors, false)) {
            throw new Exception($errors ? implode(', ', $errors) : 'Ticket transfer failed');
        }
        return true;
    }

    private function executeChangeOwner($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.user.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        $user = User::lookup($payload['user_id'] ?? 0);
        if (!$user) throw new Exception('User not found');
        // changeOwner expects a User object
        $errors = array();
        if (!$ticket->changeOwner($user)) {
            throw new Exception('Failed to change ticket owner');
        }
        return true;
    }

    // -----------------------------------------------------------------------
    // Task executors
    // -----------------------------------------------------------------------
    private function executeAssignTask($payload) {
        require_once(INCLUDE_DIR . 'class.task.php');
        require_once(INCLUDE_DIR . 'class.staff.php');
        $task = Task::lookup($payload['task_id'] ?? 0);
        if (!$task) throw new Exception('Task not found: ' . ($payload['task_id'] ?? 'null'));
        $staff = Staff::lookup($payload['staff_id'] ?? 0);
        if (!$staff) throw new Exception('Staff not found: ' . ($payload['staff_id'] ?? 'null'));
        // osTicket Task::assignToStaff signature: ($staff, $note = '', $alert = true)
        $result = $task->assignToStaff($staff, 'Assigned via FastAPI integration', false);
        if (!$result)
            throw new Exception('Task assign failed');
        return true;
    }

    private function executeCloseTask($payload) {
        require_once(INCLUDE_DIR . 'class.task.php');
        $task = Task::lookup($payload['task_id'] ?? 0);
        if (!$task) throw new Exception('Task not found');
        // Tasks use setStatus(), not a close() method — find the closed state.
        $status = TaskStatus::objects()->filter(array('state' => 'closed'))->first();
        if (!$status) throw new Exception('No task status configured with state=closed');
        $errors = [];
        if (!$task->setStatus($status, $errors))
            throw new Exception($errors ? implode(', ', $errors) : 'Task close failed');
        return true;
    }

    private function executeReopenTask($payload) {
        require_once(INCLUDE_DIR . 'class.task.php');
        $task = Task::lookup($payload['task_id'] ?? 0);
        if (!$task) throw new Exception('Task not found');
        // Tasks use setStatus(), not a reopen() method — find the open state.
        $status = TaskStatus::objects()->filter(array('state' => 'open'))->first();
        if (!$status) throw new Exception('No task status configured with state=open');
        $errors = [];
        if (!$task->setStatus($status, $errors))
            throw new Exception($errors ? implode(', ', $errors) : 'Task reopen failed');
        return true;
    }

    private function executeAddTaskNote($payload) {
        require_once(INCLUDE_DIR . 'class.task.php');
        $task = Task::lookup($payload['task_id'] ?? 0);
        if (!$task) throw new Exception('Task not found');
        $note = $payload['message'] ?? '';
        if (!$note) throw new Exception('Note message is empty');
        return (bool) $task->logNote($payload['title'] ?? 'Task Note', $note, 'SYSTEM', false);
    }

    // -----------------------------------------------------------------------
    // Ticket update / delete executors
    // -----------------------------------------------------------------------
    private function executeDeleteTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');
        // Calls osTicket's native delete which handles cascade (thread, tasks, etc.)
        if (!$ticket->delete())
            throw new Exception('Ticket delete failed');
        return true;
    }

    private function executeUpdateTicket($payload) {
        require_once(INCLUDE_DIR . 'class.ticket.php');
        require_once(INCLUDE_DIR . 'class.forms.php');
        $ticket = Ticket::lookup($payload['ticket_id'] ?? 0);
        if (!$ticket) throw new Exception('Ticket not found');

        $errors = [];
        $vars   = [];

        if (!empty($payload['subject']))     $vars['subject']     = $payload['subject'];
        if (!empty($payload['priority_id'])) $vars['priority_id'] = (int)$payload['priority_id'];
        if (!empty($payload['topic_id']))    $vars['topic_id']    = (int)$payload['topic_id'];
        if (!empty($payload['sla_id']))      $vars['sla_id']      = (int)$payload['sla_id'];
        if (!empty($payload['due_date']))    $vars['duedate']     = $payload['due_date'];

        if ($vars && !$ticket->update($vars, $errors))
            throw new Exception($errors ? implode(', ', $errors) : 'Ticket update failed');
        return true;
    }

    // -----------------------------------------------------------------------
    // User create / update executors
    // -----------------------------------------------------------------------
    private function executeCreateUser($payload) {
        require_once(INCLUDE_DIR . 'class.user.php');
        $vars = [
            'name'  => $payload['name']  ?? '',
            'email' => $payload['email'] ?? '',
        ];
        if (!empty($payload['phone'])) $vars['phone'] = $payload['phone'];

        $errors = [];
        $user = User::fromVars($vars, $errors);
        if (!$user)
            throw new Exception($errors ? implode(', ', $errors) : 'User create failed');
        return true;
    }

    private function executeUpdateUser($payload) {
        require_once(INCLUDE_DIR . 'class.user.php');
        $user = User::lookup($payload['user_id'] ?? 0);
        if (!$user) throw new Exception('User not found');

        $errors = [];
        $vars = [];
        if (!empty($payload['name']))  $vars['name']  = $payload['name'];
        if (!empty($payload['email'])) $vars['email'] = $payload['email'];
        if (!empty($payload['phone'])) $vars['phone'] = $payload['phone'];

        if ($vars && !$user->update($vars, $errors))
            throw new Exception($errors ? implode(', ', $errors) : 'User update failed');
        return true;
    }

    private function executeSetUserOrg($payload) {
        require_once(INCLUDE_DIR . 'class.user.php');
        require_once(INCLUDE_DIR . 'class.organization.php');
        $user = User::lookup($payload['user_id'] ?? 0);
        if (!$user) throw new Exception('User not found');
        $orgId = (int)($payload['org_id'] ?? 0);
        if ($orgId === 0) {
            // Remove from organization
            $user->setOrganization(null);
        } else {
            $org = Organization::lookup($orgId);
            if (!$org) throw new Exception('Organization not found');
            $user->setOrganization($org);
        }
        return true;
    }

    // Signal handlers
    // -----------------------------------------------------------------------
    function onTaskCreated($task, $data=null) {
        if (!$task) return;
        $key = 'task.created:' . $task->getId();
        $payload = array(
            'id'        => $task->getId(),
            'number'    => $task->getNumber(),
            'title'     => $task->getTitle(),
            'ticket_id' => ($task->object_type === 'T') ? $task->object_id : null,
        );
        $this->writeOutbox('task.created', 'task', $task->getId(), $payload, $key);
    }

    function onThreadEntryCreated($entry, $data=null) {
        if (!$entry) return;
        $thread = $entry->getThread();
        if (!$thread) return;

        $key = 'threadentry.created:' . $entry->getId();
        $payload = array(
            'id' => $entry->getId(),
            'thread_id' => $thread->getId(),
            'object_id' => $thread->getObjectId(),
            'object_type' => $thread->getObjectType(),
            'type' => $entry->getType(), // M, R, N
            'poster' => $entry->getPoster(),
            'body' => (string) $entry->getBody(),
            'user_id' => $entry->getUserId(),
            'staff_id' => $entry->getStaffId(),
        );
        $this->writeOutbox('threadentry.created', 'threadentry', $entry->getId(), $payload, $key);
    }

    function onOrganizationCreated($org, $data=null) {
        if (!$org) return;
        $key = 'organization.created:' . $org->getId();
        $payload = array(
            'id' => $org->getId(),
            'name' => $org->getName(),
        );
        $this->writeOutbox('organization.created', 'organization', $org->getId(), $payload, $key);
    }

    function onTicketCreated($ticket, $data=null) {
        if (!$ticket) return;
        // Idempotency key: one event per ticket creation, ever
        $key = 'ticket.created:' . $ticket->getId();

        // Include the opening message body so downstream consumers (e.g. AI
        // triage) have real content to work with, not just the subject.
        // Best-effort only — never let this block the outbox write.
        $message = '';
        try {
            if (($entry = $ticket->getLastMessage()) && ($body = $entry->getBody()))
                $message = (string) $body->getClean();
        } catch (Throwable $e) {
            // swallow — message is a nice-to-have, not required
        }

        $this->writeOutbox('ticket.created', 'ticket', $ticket->getId(), array(
            'ticket_id' => $ticket->getId(),
            'number'    => $ticket->getNumber(),
            'subject'   => $ticket->getSubject(),
            'message'   => $message,
        ), $key);
    }

    function onObjectEdited($object, $data=null) {
        if (!$object || !method_exists($object, 'getId')) return;
        $class = strtolower(get_class($object));
        $this->writeOutbox($class . '.updated', $class, $object->getId(), array(
            'id'      => $object->getId(),
            'changes' => is_array($data) ? $data : array(),
        ));
    }

    function onObjectDeleted($object, $data=null) {
        if (!$object || !method_exists($object, 'getId')) return;
        $class = strtolower(get_class($object));
        $this->writeOutbox($class . '.deleted', $class, $object->getId(), array(
            'id' => $object->getId(),
        ));
    }



    function onUserCreated($user, $data=null) {
        if (!$user) return;
        $this->writeOutbox('user.created', 'user', $user->getId(), array(
            'user_id' => $user->getId(),
            'name'    => method_exists($user, 'getName') ? (string)$user->getName() : '',
            'email'   => method_exists($user, 'getEmail') ? (string)$user->getEmail() : '',
        ), 'user.created:' . $user->getId());
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------
    private function updateInboxStatus($id, $status, $errorMsg, $retryCount=null) {
        $fields = array(
            "status='" . db_input($status, false) . "'",
            "locked_at=NULL",
        );
        if ($errorMsg !== null)
            $fields[] = "error_message='" . db_input($errorMsg, false) . "'";
        else
            $fields[] = "error_message=NULL";
        if ($retryCount !== null)
            $fields[] = "retry_count=" . (int)$retryCount;
        if ($status === 'processed' || $status === 'failed')
            $fields[] = "processed_at=NOW()";
        else
            $fields[] = "processed_at=NULL";
        db_query("UPDATE `" . TABLE_PREFIX . "integration_inbox` SET " . implode(', ', $fields) . " WHERE id=" . (int)$id, false);
    }

    private function deadLetter($sourceTable, $row, $errorMsg) {
        db_query("INSERT INTO `" . TABLE_PREFIX . "integration_dead_letter`
                    (source_table, source_id, event_type, payload, error_message)
                  VALUES (
                    '" . db_input($sourceTable, false) . "',
                    " . (int)$row['id'] . ",
                    '" . db_input($row['command_type'] ?? $row['event_type'] ?? 'unknown', false) . "',
                    '" . db_input($row['payload'], false) . "',
                    '" . db_input($errorMsg, false) . "'
                  )", false);
    }

    private function logError($message) {
        error_log('[FastAPI Integration Plugin] ' . $message);
    }
}

class FastApiIntegrationConfig extends PluginConfig {
    function getOptions() {
        // The integration secret intentionally is not configurable here. It
        // belongs in the deployment environment, not the osTicket database.
        return array(
            'verbose_logging' => new BooleanField(array(
                'label' => __('Verbose Logging'),
                'hint' => __('Log additional integration activity to the PHP error log while debugging.'),
                'default' => false,
            )),
        );
    }
}
