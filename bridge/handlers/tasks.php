<?php
/**
 * Task command handlers.
 */

require_once INCLUDE_DIR . 'class.task.php';
require_once INCLUDE_DIR . 'class.staff.php';

class FastApiTaskHandler {

    private static function requireTask($task_id) {
        $task = Task::lookup((int) $task_id);
        if (!$task) {
            throw new Exception("Task not found: {$task_id}");
        }
        return $task;
    }

    public static function create($payload, $staff_id) {
        $vars = [
            'title'       => $payload['title'] ?? 'Untitled Task',
            'description' => $payload['description'] ?? '',
            'deptId'      => $payload['dept_id'] ?? null,
        ];
        // Optionally link to a ticket
        $ticket_id = $payload['ticket_id'] ?? null;

        $errors = [];
        if ($ticket_id) {
            $ticket = Ticket::lookup((int) $ticket_id);
            if (!$ticket) {
                throw new Exception("Ticket not found: {$ticket_id}");
            }
            $task = $ticket->addTask($vars, $errors);
        } else {
            $task = Task::create($vars, $errors);
        }

        if (!$task) {
            throw new Exception('Task::create failed: ' . implode(', ', $errors));
        }
        return ['task_id' => $task->getId(), 'number' => $task->getNumber()];
    }

    public static function assign($payload, $staff_id) {
        $task  = self::requireTask($payload['task_id'] ?? 0);
        $assignee = Staff::lookup((int) ($payload['staff_id'] ?? 0));
        if (!$assignee) {
            throw new Exception("Staff not found: " . ($payload['staff_id'] ?? 'none'));
        }
        $errors = [];
        if (!$task->assignToStaff($assignee, $errors)) {
            throw new Exception('task assign failed: ' . implode(', ', $errors));
        }
        return ['task_id' => $task->getId(), 'assignee_staff_id' => $assignee->getId()];
    }

    public static function close($payload, $staff_id) {
        $task = self::requireTask($payload['task_id'] ?? 0);
        if (!$task->close()) {
            throw new Exception('Task::close() failed.');
        }
        return ['task_id' => $task->getId(), 'status' => 'closed'];
    }

    public static function delete($payload, $staff_id) {
        $task = self::requireTask($payload['task_id'] ?? 0);
        if (!$task->delete()) {
            throw new Exception('Task::delete() failed.');
        }
        return ['task_id' => (int) ($payload['task_id'] ?? 0)];
    }
}
