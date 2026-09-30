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
require_once INCLUDE_DIR . 'class.thread_actions.php';
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

    public static function create($payload, $staff_id) {
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
            'source'     => 'API',
            'ip'         => '127.0.0.1',
        ];

        $errors = [];
        // autorespond=false, alertstaff=false: creating a ticket through the API sends no email.
        $ticket = Ticket::create($vars, $errors, 'API', false, false);
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
        ];
        $errors = [];
        // claim=false: a reply through the API must not silently assign the ticket.
        $entry = $ticket->postReply($vars, $errors, true, false);
        if (!$entry || $errors) {
            throw new Exception('Reply was not posted: ' . self::errorText($errors));
        }
        return ['ticket_id' => $ticket->getId(), 'entry_id' => $entry->getId()];
    }

    public static function addNote($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $message = trim((string) ($payload['message'] ?? ''));
        if ($message === '') {
            throw new Exception("A note 'message' is required.");
        }
        $errors = [];
        $entry = $ticket->postNote(['note' => $message, 'title' => (string) ($payload['title'] ?? '')], $errors, $thisstaff, true);
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
        $entry = ThreadEntry::lookup((int) $entry_id);
        if (!$entry || !$entry->getThread() || $entry->getThread()->getObjectId() != $ticket->getId()
                || $entry->getThread()->getObjectType() !== 'T') {
            throw new Exception("Thread entry not found on this ticket: {$entry_id}");
        }
        if (!$thisstaff || !$ticket->checkStaffPerm($thisstaff)) {
            throw new Exception('Permission denied: the requesting staff member may not open this ticket.');
        }
        return $entry;
    }

    /**
     * Edit a thread entry: TEA_EditThreadEntry::updateEntry(), the code behind the thread's
     * "Edit" action. It writes a new entry (child of the old) and hides the old one, so the
     * entry id changes: the result carries new_entry_id. Who may edit is the action's own
     * isEnabled() (own post, department manager, or the thread.edit role permission). With
     * resend (replies only) the edited reply is also emailed again, as "Edit and Resend".
     * Payload: entry_id, body, title, resend, signature (none|mine|theirs|dept).
     */
    public static function editEntry($payload, $staff_id) {
        global $thisstaff;
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $entry = self::requireEntry($ticket, $payload['entry_id'] ?? 0);
        $resend = !empty($payload['resend']);
        $action = $resend ? new TEA_EditAndResendThreadEntry($entry) : new TEA_EditThreadEntry($entry);
        // osTicket: system posts cannot be edited; only staff replies can be resent
        if (!($entry->staff_id || $entry->user_id)) {
            throw new Exception('System entries cannot be edited.');
        }
        if ($resend && !($entry->staff_id && $entry->type == 'R')) {
            throw new Exception('Only a staff reply can be resent.');
        }
        if (!$action->isEnabled()) {
            throw new Exception('Permission denied: the requesting staff member may not edit this entry.');
        }
        $body = (string) ($payload['body'] ?? '');
        if (trim($body) === '') {
            throw new Exception("An entry 'body' is required.");
        }
        $_POST = ['body' => $body, 'title' => (string) ($payload['title'] ?? $entry->title),
                  'signature' => (string) ($payload['signature'] ?? 'none')];
        $_SERVER['REMOTE_ADDR'] = $_SERVER['REMOTE_ADDR'] ?? '127.0.0.1';
        $new = $action->updateEntry($resend);
        if (!$new) {
            throw new Exception('Entry was not edited.');
        }
        $changed = $new->getId() != $entry->getId();
        if ($resend) {
            $action->resend($new);
        }
        return ['ticket_id' => $ticket->getId(), 'entry_id' => (int) $entry->getId(),
                'new_entry_id' => (int) $new->getId(), 'changed' => $changed, 'resent' => $resend];
    }

    /** Resend a staff reply to the ticket's recipients: TEA_ResendThreadEntry (enabled for any agent who can open the ticket). */
    public static function resendEntry($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $entry = self::requireEntry($ticket, $payload['entry_id'] ?? 0);
        if (!($entry->staff_id && $entry->type == 'R')) {
            throw new Exception('Only a staff reply can be resent.');
        }
        $_POST = ['signature' => (string) ($payload['signature'] ?? 'none')];
        (new TEA_ResendThreadEntry($entry))->resend($entry);
        return ['ticket_id' => $ticket->getId(), 'entry_id' => (int) $entry->getId()];
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
}
