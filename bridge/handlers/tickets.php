<?php
/**
 * Ticket command handlers.
 *
 * osTicket's Ticket API is written for the staff web UI, so these mirror what
 * scp/tickets.php does, without a web request. The acting staff member is the global
 * $thisstaff (set by the dispatcher from the request's staff id); osTicket's own
 * per-ticket permission check (checkStaffPerm) is applied where its methods don't.
 *
 * - close / reopen: Ticket::setStatus() with the standard statuses (there is no close()).
 * - assign / assign-team / transfer: an AssignmentForm / TransferForm, as the UI submits.
 * - merge: Ticket::merge() takes public ticket *numbers* (the first is the parent) and a
 *   mode; the children are combined into the parent and closed.
 * - create: no customer/staff emails are sent (autorespond and alerts are off).
 *
 * Throws Exception on any error — the plugin.php dispatcher handles logging and marks
 * the inbox row as failed.
 */

require_once INCLUDE_DIR . 'class.ticket.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.dept.php';
require_once INCLUDE_DIR . 'class.team.php';
require_once INCLUDE_DIR . 'class.user.php';
require_once INCLUDE_DIR . 'class.sla.php';
require_once INCLUDE_DIR . 'class.topic.php';
require_once INCLUDE_DIR . 'class.priority.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiTicketHandler {

    private static function requireTicket($ticket_id) {
        $ticket = Ticket::lookup((int) $ticket_id);
        if (!$ticket) {
            throw new Exception("Ticket not found: {$ticket_id}");
        }
        return $ticket;
    }

    /** osTicket's own permission check for the acting staff member, by department/assignment. */
    private static function requirePerm(Ticket $ticket, $perm, $what) {
        global $thisstaff;
        if (!$thisstaff || !$ticket->checkStaffPerm($thisstaff, $perm)) {
            throw new Exception("Permission denied: the requesting staff member may not {$what} this ticket.");
        }
    }

    /**
     * The status a reply or note may also set (osTicket's "Ticket Status" select). Ticket::setStatus() returns false
     * silently when the agent may not (closing needs ticket.close, deleting ticket.delete), after the entry is posted,
     * so check first.
     */
    private static function statusChoice(Ticket $ticket, $status_id) {
        if (empty($status_id)) {
            return null;
        }
        $status = TicketStatus::lookup((int) $status_id);
        if (!$status || !$status->isEnabled()) {
            throw new Exception('Ticket status not found: ' . $status_id);
        }
        if ($status->getId() === $ticket->getStatusId()) {
            return $status->getId();
        }
        switch ($status->getState()) {
            case 'closed':
                self::requirePerm($ticket, Ticket::PERM_CLOSE, 'close');
                break;
            case 'deleted':
                self::requirePerm($ticket, Ticket::PERM_DELETE, 'delete');
                break;
            case 'archived':
                throw new Exception("Ticket status '" . $status->getName() . "' cannot be set from a reply or note.");
        }
        return $status->getId();
    }

    private static function requireStaff($staff_id) {
        $staff = Staff::lookup((int) $staff_id);
        if (!$staff) {
            throw new Exception("Staff not found: {$staff_id}");
        }
        return $staff;
    }

    private static function requireUser($user_id) {
        $user = User::lookup((int) $user_id);
        if (!$user) {
            throw new Exception("User not found: {$user_id}");
        }
        return $user;
    }

    private static function errorText(array $errors) {
        return $errors ? json_encode($errors) : 'the operation was refused (permissions, or nothing to change)';
    }

    /**
     * Build and validate the assignment form the UI would submit. Validating first gives the
     * real reason (e.g. "Team does not have members") instead of a bare "Unknown assignee".
     */
    private static function assignmentForm($assignee, $note) {
        if ($assignee[0] === 't' && ($team = Team::lookup((int) substr($assignee, 1)))) {
            if (!$team->isActive()) {
                throw new Exception("Assignment refused: team '{$team->getName()}' is disabled.");
            }
            if (!$team->getNumMembers()) {
                throw new Exception("Assignment refused: team '{$team->getName()}' does not have members.");
            }
        }
        $form = AssignmentForm::instantiate(['assignee' => $assignee, 'comments' => (string) $note]);
        if (!$form->isValid()) {
            throw new Exception('Assignment refused: ' . json_encode($form->errors()));
        }
        return $form;
    }

    /** The standard status for a state: the one named $preferred, else the first of that state. */
    private static function statusFor($state, $preferred) {
        $named = TicketStatus::objects()->filter(['state' => $state, 'name' => $preferred])->first();
        $status = $named ?: TicketStatus::objects()->filter(['state' => $state])->order_by('id')->first();
        if (!$status) {
            throw new Exception("No ticket status with state '{$state}' exists.");
        }
        return $status;
    }

    /**
     * Create: what scp/tickets.php "open" does — Ticket::open(), the staff path (not the customer-facing
     * Ticket::create() alone): it checks the create permission in the *chosen department's* role, validates
     * source / assignment / due date, and also posts the optional first response and internal note, assigns,
     * adds collaborators and records the SLA. Nothing is emailed unless 'notify' asks (reply-to).
     * Payload: subject, message, user_id | (name, email), phone, cc_user_ids, source, dept_id, topic_id, sla_id,
     * priority_id, due_date (UTC), assign_agent_id | assign_team_id, status_id, response, note, signature,
     * notify (none|user|all), custom_fields.
     */
    public static function create($payload, $staff_id) {
        global $thisstaff, $cfg;
        $subject = trim((string) ($payload['subject'] ?? ''));
        if ($subject === '') {
            throw new Exception("Ticket 'subject' is required.");
        }
        $vars = [
            'subject'    => $subject,
            'message'    => (string) ($payload['message'] ?? ''),
            'email'      => trim((string) ($payload['email'] ?? '')),
            'name'       => trim((string) ($payload['name'] ?? '')),
            'phone'      => (string) ($payload['phone'] ?? ''),
            'deptId'     => $payload['dept_id'] ?? null,
            'topicId'    => $payload['topic_id'] ?? null,
            'priorityId' => $payload['priority_id'] ?? null,
            'source'     => (string) ($payload['source'] ?? 'API'),
            'ip'         => '127.0.0.1',
            'reply-to'   => (string) ($payload['notify'] ?? 'none'),
            'signature'  => (string) ($payload['signature'] ?? 'none'),
        ];
        // osTicket's form preselects the system's default help topic
        if (empty($vars['topicId'])) {
            $vars['topicId'] = $cfg->getDefaultTopicId() ?: null;
        }
        if (!in_array($vars['reply-to'], ['none', 'user', 'all'], true)) {
            throw new Exception("'notify' must be none, user or all.");
        }

        if (!empty($payload['user_id'])) {
            $user = self::requireUser($payload['user_id']);
            $vars['uid'] = $user->getId();
        }
        // Collaborators: Ticket::addCollaborators() silently skips an unknown id, so check them first.
        $ccs = [];
        foreach ((array) ($payload['cc_user_ids'] ?? []) as $cc) {
            $ccs[] = self::requireUser($cc)->getId();
        }
        if ($ccs) {
            $vars['ccs'] = $ccs;
        }
        if (!empty($payload['sla_id'])) {
            if (!SLA::lookup((int) $payload['sla_id'])) {
                throw new Exception('SLA plan not found: ' . $payload['sla_id']);
            }
            $vars['slaId'] = (int) $payload['sla_id'];
        }
        if (!empty($payload['status_id'])) {
            $status = TicketStatus::lookup((int) $payload['status_id']);
            if (!$status) {
                throw new Exception('Ticket status not found: ' . $payload['status_id']);
            }
            // the staff form offers the enabled open statuses only
            if (!$status->isEnabled() || $status->getState() !== 'open') {
                throw new Exception("Ticket status '" . $status->getName() . "' cannot be chosen for a new ticket (enabled open statuses only).");
            }
            $vars['statusId'] = (int) $payload['status_id'];
        }
        if (!empty($payload['assign_agent_id'])) {
            $vars['assignId'] = 's' . self::requireStaff($payload['assign_agent_id'])->getId();
        } elseif (!empty($payload['assign_team_id'])) {
            $team = Team::lookup((int) $payload['assign_team_id']);
            if (!$team) {
                throw new Exception('Team not found: ' . $payload['assign_team_id']);
            }
            $vars['assignId'] = 't' . $team->getId();
        }
        // The form takes the due date as a string in osTicket's configured timezone; the payload carries UTC.
        if (!empty($payload['due_date'])) {
            $vars['duedate'] = (new DateTime($payload['due_date'], new DateTimeZone('UTC')))
                ->setTimezone(new DateTimeZone($cfg->getTimezone()))->format('Y-m-d H:i:s');
        }

        // The first response needs ticket.reply in the department the ticket will land in; Ticket::open() would
        // skip it silently, so refuse before anything is created.
        $response = trim((string) ($payload['response'] ?? ''));
        $note = trim((string) ($payload['note'] ?? ''));
        if ($response !== '') {
            $dept_id = (int) ($vars['deptId'] ?: (($t = Topic::lookup((int) $vars['topicId'])) ? $t->getDeptId() : 0)
                ?: $cfg->getDefaultDeptId());
            $role = $thisstaff ? $thisstaff->getRole($dept_id) : null;
            if (!$role || !$role->hasPerm(Ticket::PERM_REPLY)) {
                throw new Exception('Permission denied: the requesting staff member may not reply to tickets in this department.');
            }
            $vars['response'] = $response;
        }
        if ($note !== '') {
            $vars['note'] = $note;
        }

        // Files on the first message: Ticket::open() reads them from the message field's upload widget, which takes
        // them from the form data under 'attach:<field id>' as file id => name (as the browser form posts them).
        $uploads = FastApiAttachments::upload((array) ($payload['attachments'] ?? []));
        if ($uploads) {
            $messageField = TicketForm::getNewInstance()->getField('message');
            $vars['attach:' . $messageField->get('id')] = array_column($uploads, 'name', 'id');
            // the widget keeps only files "uploaded in this session" (osTicket's ajax upload marks them so)
            $_SESSION = $_SESSION ?? [];
            foreach ($uploads as $u) {
                $_SESSION[':uploadedFiles'][$u['id']] = $u['name'];
            }
        }

        // Custom fields are read from the same vars by their variable names (topic forms included).
        // Only names that are real fields of the ticket form or the chosen topic's forms get through, and never
        // a key Ticket::open() / create() reads to decide assignment, SLA, collaborators, status or alerts.
        $custom = (array) ($payload['custom_fields'] ?? []);
        if ($custom) {
            $forms = [TicketForm::getNewInstance()];
            if (!empty($vars['topicId']) && ($topic = Topic::lookup($vars['topicId']))) {
                foreach ($topic->getForms() as $f) {
                    if ($f->get('type') !== 'T') {
                        $forms[] = $f->instanciate(0);
                    }
                }
            }
            $core = ['subject', 'message', 'email', 'name', 'phone', 'priority', 'topic', 'topicId', 'duedate'];
            foreach (FastApiModel::checkCreateCustomFields($custom, $forms, $core) as $name => $value) {
                $vars[$name] = $value;
            }
        }

        $errors = [];
        $_SERVER['REMOTE_ADDR'] = $_SERVER['REMOTE_ADDR'] ?? '127.0.0.1';
        $ticket = Ticket::open($vars, $errors);
        if (!$ticket || $errors) {
            throw new Exception('Ticket was not created: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'number' => $ticket->getNumber()];
    }

    public static function reply($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_REPLY, 'reply to');
        $message = trim((string) ($payload['message'] ?? ''));
        if ($message === '') {
            throw new Exception("A reply 'message' is required.");
        }
        $signature = $payload['signature'] ?? '';
        $vars = [
            'response'  => $message,
            // osTicket's signature choices are none | mine | dept
            'signature' => in_array($signature, ['none', 'mine', 'dept'], true) ? $signature : 'none',
            'files'     => array_merge(FastApiAttachments::upload((array) ($payload['attachments'] ?? [])),
                                       self::cannedFiles($payload)),
            'reply_status_id' => self::statusChoice($ticket, $payload['status_id'] ?? null),
        ];
        $errors = [];
        // claim=false: a reply through the API must not silently assign the ticket.
        $entry = $ticket->postReply($vars, $errors, true, false);
        if (!$entry || $errors) {
            throw new Exception('Reply was not posted: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'entry_id' => $entry->getId()];
    }

    /**
     * The files of the premade reply the agent picked, attached to the reply the way osTicket's reply box does (its
     * script adds the response's files to the attachment field). Each id must be a separate file of an enabled
     * premade reply; the API has already checked the agent may use it.
     */
    private static function cannedFiles($payload) {
        $ids = array_values(array_unique(array_map('intval', (array) ($payload['canned_attachment_ids'] ?? []))));
        if (!$ids) {
            return [];
        }
        require_once INCLUDE_DIR . 'class.canned.php';
        $canned = Canned::lookup((int) ($payload['canned_id'] ?? 0));
        if (!$canned || !$canned->isEnabled()) {
            throw new Exception('Premade reply not found: ' . (int) ($payload['canned_id'] ?? 0));
        }
        $have = [];
        foreach ([false, true] as $inline) {      // what the HTML box lists, and the images the plain box lists
            foreach ($canned->getAttachedFiles($inline) as $file) {
                $have[(int) $file->id] = (string) $file->name;
            }
        }
        $files = [];
        foreach ($ids as $id) {
            if (!isset($have[$id])) {
                throw new Exception("File {$id} is not attached to premade reply " . (int) $canned->getId() . '.');
            }
            $files[] = ['id' => $id, 'name' => $have[$id]];
        }
        return $files;
    }

    public static function addNote($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $message = trim((string) ($payload['message'] ?? ''));
        if ($message === '') {
            throw new Exception("A note 'message' is required.");
        }
        $errors = [];
        $entry = $ticket->postNote([
            'note' => $message, 'title' => (string) ($payload['title'] ?? ''),
            'note_status_id' => self::statusChoice($ticket, $payload['status_id'] ?? null),
            'files' => FastApiAttachments::upload((array) ($payload['attachments'] ?? [])),
        ], $errors, $thisstaff, true);
        if (!$entry || $errors) {
            throw new Exception('Note was not posted: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'entry_id' => $entry->getId()];
    }

    private static function changeStatus($payload, $state, $preferred, $perm, $what) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, $perm, $what);
        if ($ticket->getStatus()->getState() === $state) {
            throw new Exception("Ticket is already {$state}.");
        }
        $errors = [];
        if (!$ticket->setStatus(self::statusFor($state, $preferred), (string) ($payload['note'] ?? ''), $errors) || $errors) {
            throw new Exception("Ticket could not be {$state}: " . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'status' => $state];
    }

    public static function close($payload, $staff_id) {
        return self::changeStatus($payload, 'closed', 'Closed', Ticket::PERM_CLOSE, 'close');
    }

    public static function reopen($payload, $staff_id) {
        return self::changeStatus($payload, 'open', 'Open', Ticket::PERM_CLOSE, 'reopen');
    }

    public static function assign($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_ASSIGN, 'assign');
        $assignee = self::requireStaff($payload['staff_id'] ?? 0);
        $form = self::assignmentForm('s' . $assignee->getId(), $payload['note'] ?? '');
        $errors = [];
        if (!$ticket->assign($form, $errors, true) || $errors) {
            throw new Exception('Ticket was not assigned: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'assignee_staff_id' => $assignee->getId()];
    }

    public static function assignTeam($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_ASSIGN, 'assign');
        $team = Team::lookup((int) ($payload['team_id'] ?? 0));
        if (!$team) {
            throw new Exception('Team not found: ' . ($payload['team_id'] ?? 'none'));
        }
        $form = self::assignmentForm('t' . $team->getId(), $payload['note'] ?? '');
        $errors = [];
        if (!$ticket->assign($form, $errors, true) || $errors) {
            throw new Exception('Ticket was not assigned to the team: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'assignee_team_id' => $team->getId()];
    }

    public static function transfer($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_TRANSFER, 'transfer');
        $dept = Dept::lookup((int) ($payload['dept_id'] ?? 0));
        if (!$dept || !$dept->isActive()) {
            throw new Exception('Department not found or not active: ' . ($payload['dept_id'] ?? 'none'));
        }
        $form = TransferForm::instantiate(['dept' => $dept->getId(), 'comments' => (string) ($payload['note'] ?? '')]);
        $errors = [];
        if (!$ticket->transfer($form, $errors, false) || $errors) {
            throw new Exception('Ticket was not transferred: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'dept_id' => $dept->getId()];
    }

    public static function changeOwner($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $user = self::requireUser($payload['user_id'] ?? 0);
        if ($user->getId() == $ticket->getOwnerId()) {
            throw new Exception('That user already owns the ticket.');
        }
        if (!$ticket->changeOwner($user)) {
            throw new Exception('Owner was not changed (the requesting staff member needs the ticket edit permission).');
        }
        return ['ticket_id' => $ticket->getId(), 'user_id' => $user->getId()];
    }

    /**
     * PATCH: what scp/tickets.php?a=edit does. Ticket::update() overwrites topic, SLA, source and
     * due date from the submitted form, so the vars start from the stored state and only the
     * keys present in the payload are overlaid. Subject and priority are ticket-form answers,
     * not columns: they are set on the stored form entries before update() saves the answers.
     * Payload keys: topic_id, sla_id (0 = none), due_date (UTC 'Y-m-d H:i:s'; null = clear), source,
     * subject, priority_id, note. update() keeps every form entry (forms = all entry ids), so
     * none is deleted.
     */
    public static function update($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_EDIT, 'edit');

        $forms = [];
        $entries = DynamicFormEntry::forTicket($ticket->getId());
        foreach ($entries as $entry) {
            $entry->addMissingFields();
            $forms[] = $entry->getId();
        }

        $answers = [];
        if (array_key_exists('subject', $payload)) {
            $answers['subject'] = (string) $payload['subject'];
        }
        if (array_key_exists('priority_id', $payload)) {
            if (!Priority::lookup((int) $payload['priority_id'])) {
                throw new Exception('Priority not found: ' . $payload['priority_id']);
            }
            $answers['priority'] = (int) $payload['priority_id'];
        }
        // Ticket::update() only validates the shape of these ids, not that they exist.
        if (array_key_exists('topic_id', $payload) && !Topic::lookup((int) $payload['topic_id'])) {
            throw new Exception('Help topic not found: ' . $payload['topic_id']);
        }
        if (!empty($payload['sla_id']) && !SLA::lookup((int) $payload['sla_id'])) {
            throw new Exception('SLA plan not found: ' . $payload['sla_id']);
        }
        $errors = [];
        foreach ($answers as $name => $value) {
            $done = false;
            foreach ($entries as $entry) {
                if (!($field = $entry->getField($name)))
                    continue;
                $clean = $name === 'priority' ? [$value => Priority::lookup($value)->getDesc()] : $field->parse($value);
                $field->validateEntry($clean);
                if ($field->errors()) {
                    $errors[$name] = $field->errors();
                } else {
                    $entry->setAnswer($name, $name === 'priority' ? Priority::lookup($value)->getDesc() : $clean,
                        $name === 'priority' ? $value : null);
                }
                $done = true;
                break;
            }
            if (!$done) {
                $errors[$name] = ["The ticket has no '{$name}' field"];
            }
        }
        // Custom fields: any editable field of the ticket's forms (the ticket form and its help topic's forms).
        foreach ((array) ($payload['custom_fields'] ?? []) as $name => $value) {
            $errs = ["The ticket has no '{$name}' field"];
            foreach ($entries as $entry) {
                if ($entry->getField($name)) {
                    $errs = FastApiModel::setEntryAnswer($entry, $name, $value);
                    break;
                }
            }
            if ($errs) {
                $errors[$name] = $errs;
            }
        }
        if ($errors) {
            throw new Exception('Ticket was not updated: ' . json_encode($errors));
        }

        // osTicket's form takes the due date as a string in its configured timezone. The payload
        // carries UTC; an unchanged stored date is passed back (osTicket refuses a past one,
        // as its own page does).
        global $cfg;
        $tz = $cfg->getTimezone();
        if (array_key_exists('due_date', $payload)) {
            $duedate = $payload['due_date']
                ? (new DateTime($payload['due_date'], new DateTimeZone('UTC')))
                    ->setTimezone(new DateTimeZone($tz))->format('Y-m-d H:i:s')
                : '';
        } else {
            $duedate = $ticket->getDueDate() ? Format::datetime($ticket->getDueDate(), true, 'yyyy-MM-dd HH:mm:ss', $tz) : ''; // ICU pattern
        }
        $vars = [
            'topicId' => array_key_exists('topic_id', $payload) ? (int) $payload['topic_id'] : (int) $ticket->getTopicId(),
            'slaId'   => array_key_exists('sla_id', $payload) ? (int) $payload['sla_id'] : (int) $ticket->getSLAId(),
            'source'  => array_key_exists('source', $payload) ? (string) $payload['source'] : (string) $ticket->getSource(),
            'duedate' => $duedate,
            'note'    => (string) ($payload['note'] ?? ''),
            'forms'   => $forms,
        ];
        if (!$ticket->update($vars, $errors) || $errors) {
            throw new Exception('Ticket was not updated: ' . self::errorText($errors));
        }
        FastApiModel::applyTicketAnswers($ticket, (array) ($payload['custom_fields'] ?? []));
        return ['ticket_id' => $ticket->getId()];
    }

    /**
     * Claim: ajax.tickets.php claim() — needs ticket.assign, an open ticket nobody holds; the
     * ClaimForm is the one osTicket builds for the acting staff member.
     */
    public static function claim($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_ASSIGN, 'claim');
        if (!$ticket->isOpen()) {
            throw new Exception('Only open tickets can be claimed.');
        }
        if ($ticket->getStaff()) {
            throw new Exception('Ticket is already assigned to ' . $ticket->getAssigned() . '.');
        }
        $form = $ticket->getClaimForm(['assignee' => ['s' . $thisstaff->getId()], 'comments' => (string) ($payload['note'] ?? '')]);
        if (!$form->isValid()) {
            throw new Exception('Claim refused: ' . json_encode($form->errors()));
        }
        $errors = [];
        if (!$ticket->claim($form, $errors) || $errors) {
            throw new Exception('Ticket was not claimed: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'assignee_staff_id' => (int) $thisstaff->getId()];
    }

    /**
     * Release (unassign): ajax.tickets.php release(). "staff" and/or "team" say which assignment
     * to drop; both is osTicket's Ticket::unassign(). Needs ticket.release (or being a manager).
     */
    public static function release($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        if (!$thisstaff || (!$ticket->checkStaffPerm($thisstaff, Ticket::PERM_RELEASE) && !$thisstaff->isManager())) {
            throw new Exception('Permission denied: the requesting staff member may not release this ticket.');
        }
        if (!$ticket->isAssigned()) {
            throw new Exception('Ticket is not assigned!');
        }
        $info = [];
        $staff = $ticket->getStaff();
        $team = $ticket->getTeam();
        if (!empty($payload['staff'])) {
            if (!$staff) {
                throw new Exception('Ticket is not assigned to an agent.');
            }
            $info['sid'] = $staff->getId();
        }
        if (!empty($payload['team'])) {
            if (!$team) {
                throw new Exception('Ticket is not assigned to a team.');
            }
            $info['tid'] = $team->getId();
        }
        if (!$info) {
            throw new Exception("Say which assignment to release: 'staff' and/or 'team'.");
        }
        $errors = [];
        if (!$ticket->release($info, $errors)) {
            throw new Exception('Assignment was not released: ' . self::errorText($errors));
        }
        $data = [];
        if ($staff && !$ticket->getStaff())
            $data['staff'] = [$staff->getId(), (string) $staff->getName()->getOriginal()];
        if ($team && !$ticket->getTeam())
            $data['team'] = $team->getId();
        $ticket->logEvent('released', $data);
        $note = trim((string) ($payload['note'] ?? ''));
        if ($note !== '') {
            $_errors = [];
            $ticket->postNote(['note' => $note, 'title' => 'Assignment Released'], $_errors, $thisstaff, false);
        }
        return ['ticket_id' => $ticket->getId(), 'released_staff' => isset($info['sid']), 'released_team' => isset($info['tid'])];
    }

    /**
     * Refer: ajax.tickets.php refer() — needs ticket.assign; the ReferralForm validates the
     * referee (agent / team / department) and Ticket::refer() records it.
     */
    public static function refer($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_ASSIGN, 'refer');
        $source = ['comments' => (string) ($payload['note'] ?? '')];
        foreach (['agent' => 'agent_id', 'team' => 'team_id', 'dept' => 'dept_id'] as $target => $key) {
            if (!empty($payload[$key])) {
                $source['target'] = $target;
                $source[$target] = (int) $payload[$key];
            }
        }
        if (empty($source['target'])) {
            throw new Exception("A referee is required ('agent_id', 'team_id' or 'dept_id').");
        }
        $form = $ticket->getReferralForm($source, ['target' => $source['target']]);
        $errors = [];
        if (!$form->isValid() || !$ticket->refer($form, $errors) || $errors) {
            throw new Exception('Ticket was not referred: ' . self::errorText($errors + $form->errors()));
        }
        $clean = $form->getClean();
        if (!empty($clean['comments'])) {
            $ticket->logNote('Referral', $clean['comments'], $thisstaff);
        }
        return ['ticket_id' => $ticket->getId(), 'referee' => $source['target'], 'referee_id' => $source[$source['target']]];
    }

    /**
     * Remove a referral: ajax.tickets.php refer() action 'manage' — needs ticket.assign; deletes the
     * thread's referral rows by id (only ones on this ticket's own thread).
     */
    public static function removeReferral($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_ASSIGN, 'refer');
        $id = (int) ($payload['referral_id'] ?? 0);
        $referral = $ticket->getThread()->referrals->filter(['id' => $id])->first();
        if (!$referral) {
            throw new Exception("Referral not found on this ticket: {$id}");
        }
        $type = $referral->object_type;
        $object_id = (int) $referral->object_id;
        if (!$ticket->getThread()->referrals->filter(['id__in' => [$id]])->delete()) {
            throw new Exception('Referral was not removed.');
        }
        return ['ticket_id' => $ticket->getId(), 'referral_id' => $id, 'referee_type' => $type, 'referee_id' => $object_id];
    }

    public static function addCollaborator($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_EDIT, 'edit');
        $user = self::requireUser($payload['user_id'] ?? 0);
        $errors = [];
        $collaborator = $ticket->addCollaborator($user, [], $errors);
        if (!$collaborator || $errors) {
            throw new Exception('Collaborator was not added: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'collaborator_user_id' => $user->getId()];
    }

    private static function requireCollaborator(Ticket $ticket, $user_id) {
        $thread = $ticket->getThread();
        $collab = $thread ? Collaborator::lookup(['thread_id' => $thread->getId(), 'user_id' => (int) $user_id]) : null;
        if (!$collab) {
            throw new Exception("User {$user_id} is not a collaborator of this ticket.");
        }
        return $collab;
    }

    /**
     * Update a collaborator's flags (Collaborator::FLAG_ACTIVE = receives the thread's mail,
     * FLAG_CC). Only the flags present in the payload change. Payload: user_id, is_active, is_cc.
     */
    public static function updateCollaborator($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_EDIT, 'edit');
        $collab = self::requireCollaborator($ticket, $payload['user_id'] ?? 0);
        $changed = false;
        foreach (['is_active' => Collaborator::FLAG_ACTIVE, 'is_cc' => Collaborator::FLAG_CC] as $key => $flag) {
            if (array_key_exists($key, $payload)) {
                $collab->setFlag($flag, (bool) $payload[$key]);
                $changed = true;
            }
        }
        if (!$changed) {
            throw new Exception("Nothing to change: give 'is_active' and/or 'is_cc'.");
        }
        if (!$collab->save()) {
            throw new Exception('Collaborator was not updated.');
        }
        return ['ticket_id' => $ticket->getId(), 'collaborator_user_id' => (int) $collab->getUserId(),
                'is_active' => $collab->isActive(), 'is_cc' => $collab->isCc()];
    }

    /** Remove a collaborator and log the "collab" event, as Thread::updateCollaborators() does. */
    public static function removeCollaborator($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_EDIT, 'edit');
        $collab = self::requireCollaborator($ticket, $payload['user_id'] ?? 0);
        $user_id = (int) $collab->getUserId();
        $thread = $ticket->getThread();
        // capture before deleting: the event needs the name
        $thread->logCollaboratorEvents($collab, ['del' => true]);
        if (!$collab->delete()) {
            throw new Exception('Collaborator was not removed.');
        }
        return ['ticket_id' => $ticket->getId(), 'collaborator_user_id' => $user_id];
    }

    /** The entry, checked to belong to this ticket's thread and to be visible to the acting staff member. */
    private static function requireEntry(Ticket $ticket, $entry_id) {
        global $thisstaff;
        $entry = FastApiThreadEntry::entryOf($ticket, 'T', $entry_id);
        if (!$thisstaff || !$ticket->checkStaffPerm($thisstaff)) {
            throw new Exception('Permission denied: the requesting staff member may not open this ticket.');
        }
        return $entry;
    }

    /**
     * Edit a thread entry (see FastApiThreadEntry::edit). The entry id changes: the result carries
     * new_entry_id. Payload: entry_id, body, title, resend, signature (none|mine|theirs|dept).
     */
    public static function editEntry($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $entry = self::requireEntry($ticket, $payload['entry_id'] ?? 0);
        return ['ticket_id' => $ticket->getId()] + FastApiThreadEntry::edit($entry, $payload);
    }

    /** Resend a staff reply to the ticket's recipients. */
    public static function resendEntry($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $entry = self::requireEntry($ticket, $payload['entry_id'] ?? 0);
        return ['ticket_id' => $ticket->getId()] + FastApiThreadEntry::resend($entry, $payload);
    }

    public static function answered($payload, $staff_id) {
        return self::setAnswered($payload, true, $staff_id);
    }

    public static function unanswered($payload, $staff_id) {
        return self::setAnswered($payload, false, $staff_id);
    }

    private static function setAnswered($payload, $answered, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_MARKANSWERED, 'mark answered/unanswered');
        if ($answered) {
            $ticket->markAnswered();
        } else {
            $ticket->markUnAnswered();
        }
        return ['ticket_id' => $ticket->getId(), 'answered' => (bool) $answered];
    }

    public static function delete($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_DELETE, 'delete');
        if (!$ticket->delete((string) ($payload['comments'] ?? ''))) {
            throw new Exception('Ticket could not be deleted.');
        }
        return ['ticket_id' => (int) ($payload['ticket_id'] ?? 0)];
    }

    public static function merge($payload, $staff_id) {
        $parent = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($parent, Ticket::PERM_MERGE, 'merge into');

        $children = array_values(array_unique(array_map('intval', (array) ($payload['child_ids'] ?? []))));
        if (!$children) {
            throw new Exception("At least one child ticket ('child_ids') is required.");
        }
        // Ticket::merge() addresses tickets by public number; the first is the parent.
        $numbers = [$parent->getNumber()];
        foreach ($children as $child_id) {
            if ($child_id === (int) $parent->getId()) {
                throw new Exception('A ticket cannot be merged into itself.');
            }
            $child = self::requireTicket($child_id);
            self::requirePerm($child, Ticket::PERM_MERGE, 'merge');
            $numbers[] = $child->getNumber();
        }

        $options = [
            'tids'           => $numbers,
            'title'          => 'merge',
            'combine'        => 1,          // 0 = separate threads, 1 = combine, 2 = link
            'participants'   => 'all',      // children's collaborators join the parent
            'childStatusId'  => self::statusFor('closed', 'Closed')->getId(),
        ];
        $result = Ticket::merge($options);
        if (!$result) {
            throw new Exception('Tickets were not merged (check that none is already part of another merge).');
        }
        return ['ticket_id' => $parent->getId(), 'merged_child_ids' => $children];
    }

    /**
     * Link tickets (osTicket's "Link Tickets": the same dialog and Ticket::merge() as merging, with title 'link' and
     * combine = 2). The threads stay apart and nothing is closed. Ticket::merge() returns false for a link although
     * manageMerge() did the work, so the result is checked on the tickets.
     */
    public static function link($payload, $staff_id) {
        $parent = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($parent, Ticket::PERM_LINK, 'link');
        $children = array_values(array_unique(array_map('intval', (array) ($payload['child_ids'] ?? []))));
        if (!$children) {
            throw new Exception("At least one ticket to link ('child_ids') is required.");
        }
        $notMerged = function (Ticket $t) {
            if ($t->getMergeType() !== 'visual' && $t->isMerged()) {
                throw new Exception("Ticket #{$t->getNumber()} is part of a merge. Merged Tickets cannot be linked.");
            }
        };
        $notMerged($parent);
        if ($parent->isChild()) {
            throw new Exception("Ticket #{$parent->getNumber()} is already linked to another ticket as a child.");
        }
        $numbers = [$parent->getNumber()];
        foreach ($children as $child_id) {
            if ($child_id === (int) $parent->getId()) {
                throw new Exception('A ticket cannot be linked to itself.');
            }
            $child = self::requireTicket($child_id);
            self::requirePerm($child, Ticket::PERM_LINK, 'link');
            $notMerged($child);
            if ($child->isParent() || ($child->isChild() && $child->getPid() != $parent->getId())) {
                throw new Exception("Ticket #{$child->getNumber()} is already linked to other tickets.");
            }
            $numbers[] = $child->getNumber();
        }
        Ticket::merge(['tids' => $numbers, 'title' => 'link', 'combine' => 2]);
        foreach ($children as $child_id) {
            $child = Ticket::lookup($child_id);
            if (!$child || (int) $child->getPid() !== (int) $parent->getId()) {
                throw new Exception("Ticket #{$child_id} was not linked.");
            }
        }
        return ['ticket_id' => $parent->getId(), 'linked_child_ids' => $children];
    }

    /** Remove a link: from a parent every child is released, from a child only itself (osTicket's Ticket::unlink()). */
    public static function unlink($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_LINK, 'unlink');
        if (!$ticket->isMerged() || $ticket->getMergeType() !== 'visual') {
            throw new Exception('Ticket is not linked to another ticket.');
        }
        $ticket->unlink();
        return ['ticket_id' => $ticket->getId()];
    }

    /**
     * Undo a merge (the merge dialog's unlink: Ticket::unlink()). Children stay in the status the merge gave them;
     * osTicket does not reopen them.
     */
    public static function unmerge($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        self::requirePerm($ticket, Ticket::PERM_MERGE, 'un-merge');
        if (!$ticket->isMerged() || $ticket->getMergeType() === 'visual') {
            throw new Exception('Ticket is not part of a merge.');
        }
        $ticket->unlink();
        return ['ticket_id' => $ticket->getId()];
    }
}
