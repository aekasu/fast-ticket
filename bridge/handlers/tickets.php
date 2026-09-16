<?php
/**
 * Ticket command handlers.
 *
 * All methods load the target Ticket via osTicket's Ticket::lookup(),
 * perform the operation through the PHP model layer, and return a result
 * array for the outbox event payload.
 *
 * Throws Exception on any error — the plugin.php dispatcher handles logging
 * and marking the inbox row as failed.
 */

require_once INCLUDE_DIR . 'class.ticket.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.dept.php';
require_once INCLUDE_DIR . 'class.team.php';

class FastApiTicketHandler {

    private static function requireTicket($ticket_id) {
        $ticket = Ticket::lookup((int) $ticket_id);
        if (!$ticket) {
            throw new Exception("Ticket not found: {$ticket_id}");
        }
        return $ticket;
    }

    private static function requireStaff($staff_id) {
        if (!$staff_id) return null;
        $staff = Staff::lookup((int) $staff_id);
        if (!$staff) {
            throw new Exception("Staff not found: {$staff_id}");
        }
        return $staff;
    }

    public static function create($payload, $staff_id) {
        // Build vars array matching osTicket's Ticket::create() expectations
        $vars = [
            'subject'    => $payload['subject'] ?? 'No Subject',
            'message'    => $payload['message'] ?? '',
            'email'      => $payload['email'] ?? '',
            'name'       => $payload['name'] ?? '',
            'phone'      => $payload['phone'] ?? '',
            'deptId'     => $payload['dept_id'] ?? null,
            'topicId'    => $payload['topic_id'] ?? null,
            'priorityId' => $payload['priority_id'] ?? null,
            'source'     => 'API',
        ];

        $errors = [];
        $ticket = Ticket::create($vars, $errors, 'API', false, false);
        if (!$ticket) {
            throw new Exception('Ticket::create failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId(), 'number' => $ticket->getNumber()];
    }

    public static function reply($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $staff  = self::requireStaff($staff_id);

        $vars = [
            'response'   => $payload['message'] ?? '',
            'signature'  => $payload['signature'] ?? '',
        ];
        $errors = [];
        if (!$ticket->postReply($vars, $errors, true, false)) {
            throw new Exception('postReply failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId()];
    }

    public static function addNote($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $vars = [
            'note'  => $payload['message'] ?? '',
            'title' => $payload['title'] ?? '',
        ];
        $errors = [];
        if (!$ticket->postNote($vars, $errors, null, true)) {
            throw new Exception('postNote failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId()];
    }

    public static function close($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        if (!$ticket->close()) {
            throw new Exception('Ticket::close() failed.');
        }
        return ['ticket_id' => $ticket->getId(), 'status' => 'closed'];
    }

    public static function reopen($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        if (!$ticket->reopen()) {
            throw new Exception('Ticket::reopen() failed.');
        }
        return ['ticket_id' => $ticket->getId(), 'status' => 'open'];
    }

    public static function assign($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $assignee = self::requireStaff($payload['staff_id'] ?? 0);
        $errors = [];
        if (!$ticket->assignToStaff($assignee, $payload['note'] ?? '', $errors)) {
            throw new Exception('assign failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId(), 'assignee_staff_id' => $assignee->getId()];
    }

    public static function assignTeam($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $team   = Team::lookup((int) ($payload['team_id'] ?? 0));
        if (!$team) {
            throw new Exception("Team not found: " . ($payload['team_id'] ?? 'none'));
        }
        $errors = [];
        if (!$ticket->assignToTeam($team, $payload['note'] ?? '', $errors)) {
            throw new Exception('assignTeam failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId(), 'assignee_team_id' => $team->getId()];
    }

    public static function transfer($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $dept   = Dept::lookup((int) ($payload['dept_id'] ?? 0));
        if (!$dept) {
            throw new Exception("Department not found: " . ($payload['dept_id'] ?? 'none'));
        }
        $vars   = ['dept_id' => $dept->getId(), 'comments' => $payload['note'] ?? ''];
        $errors = [];
        if (!$ticket->transfer($dept->getId(), $errors)) {
            throw new Exception('transfer failed: ' . implode(', ', $errors));
        }
        return ['ticket_id' => $ticket->getId(), 'dept_id' => $dept->getId()];
    }

    public static function changeOwner($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $user   = User::lookup((int) ($payload['user_id'] ?? 0));
        if (!$user) {
            throw new Exception("User not found: " . ($payload['user_id'] ?? 'none'));
        }
        if (!$ticket->changeOwner($user)) {
            throw new Exception('changeOwner failed.');
        }
        return ['ticket_id' => $ticket->getId(), 'user_id' => $user->getId()];
    }

    public static function delete($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        if (!$ticket->delete($payload['comments'] ?? '')) {
            throw new Exception('Ticket::delete() failed.');
        }
        return ['ticket_id' => (int) ($payload['ticket_id'] ?? 0)];
    }

    public static function merge($payload, $staff_id) {
        $ticket    = self::requireTicket($payload['ticket_id'] ?? 0);
        $child_ids = $payload['child_ids'] ?? [];
        $merged    = [];
        foreach ($child_ids as $child_id) {
            $child = Ticket::lookup((int) $child_id);
            if ($child && $ticket->merge($child)) {
                $merged[] = $child_id;
            }
        }
        return ['ticket_id' => $ticket->getId(), 'merged_child_ids' => $merged];
    }

    public static function setAnswered($payload, $answered, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        if ($answered) {
            $ticket->markAnswered();
        } else {
            $ticket->markUnAnswered();
        }
        return ['ticket_id' => $ticket->getId(), 'answered' => $answered];
    }

    public static function addCollaborator($payload, $staff_id) {
        $ticket = self::requireTicket($payload['ticket_id'] ?? 0);
        $user   = User::lookup((int) ($payload['user_id'] ?? 0));
        if (!$user) {
            throw new Exception("User not found: " . ($payload['user_id'] ?? 'none'));
        }
        $ticket->addCollaborator($user, [], $errors = []);
        return ['ticket_id' => $ticket->getId(), 'collaborator_user_id' => $user->getId()];
    }
}
