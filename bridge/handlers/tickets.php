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

    public static function setAnswered($payload, $answered, $staff_id) {
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
