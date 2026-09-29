<?php
/**
 * Task command handlers.
 *
 * osTicket's Task API is written for the staff web UI, so these mirror what
 * scp/ajax.tasks.php does, without a web request:
 *
 *  - create:  build the default task form (title, description) and the internal form
 *             (department, due date), validate both, and pass their cleaned data to
 *             Task::create(). That needs the acting staff member ($thisstaff, set by the
 *             dispatcher) to hold the create permission, and returns null otherwise.
 *  - update:  title is an answer of the task form (FastApiModel::applyAnswers); the
 *             description is the thread's original message; a department change is a
 *             transfer; due date is a column.
 *  - close / reopen: Task::setStatus() (Task::close()/reopen() are protected).
 *  - assign:  Task::assign() with an AssignmentForm, as Task::create() does itself.
 *
 * Payload field names match app/modules/tasks/models.py.
 */

require_once INCLUDE_DIR . 'class.task.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiTaskHandler {

    private static function requireTask($task_id) {
        $task = Task::lookup((int) $task_id);
        if (!$task) {
            throw new Exception("Task not found: {$task_id}");
        }
        return $task;
    }

    /** osTicket's own permission check for the acting staff member, by department/assignment. */
    private static function requirePerm(Task $task, $perm, $what) {
        global $thisstaff;
        if (!$thisstaff || !$task->checkStaffPerm($thisstaff, $perm)) {
            throw new Exception("Permission denied: the requesting staff member may not {$what} this task.");
        }
    }

    private static function requireDept($dept_id) {
        $dept = Dept::lookup((int) $dept_id);
        if (!$dept || !$dept->isActive()) {
            throw new Exception("Department not found or not active: {$dept_id}");
        }
        return $dept;
    }

    private static function formErrors($form) {
        $messages = [];
        foreach ((array) $form->errors() as $field => $errs) {
            $messages[$field] = $errs;
        }
        return json_encode($messages);
    }

    public static function create($payload, $staff_id) {
        global $thisstaff, $cfg;

        $title = trim((string) ($payload['title'] ?? ''));
        if ($title === '') {
            throw new Exception("Task 'title' is required.");
        }
        $dept_id = !empty($payload['dept_id']) ? self::requireDept($payload['dept_id'])->getId() : $cfg->getDefaultDeptId();

        $data = [
            'title'       => $title,
            'description' => (string) ($payload['description'] ?? ''),
            'dept_id'     => $dept_id,
            'assignee'    => '',
            'duedate'     => (string) ($payload['due_date'] ?? ''),
        ];

        $form = TaskForm::getInstance();
        $form->setSource($data);
        $iform = TaskForm::getInternalForm($data);
        if (!$iform->isValid() || !$form->isValid()) {
            throw new Exception('Task data rejected: ' . self::formErrors($form) . ' ' . self::formErrors($iform));
        }

        $vars = $data;
        $vars['default_formdata']  = $form->getClean();
        $vars['internal_formdata'] = $iform->getClean();
        $vars['description']       = $form->getField('description')->getClean();
        $vars['staffId']           = $thisstaff->getId();
        $vars['poster']            = $thisstaff;
        $vars['ip_address']        = '127.0.0.1';

        // Optionally link the task to a ticket (what "add task" on a ticket does).
        if (!empty($payload['ticket_id'])) {
            $ticket = Ticket::lookup((int) $payload['ticket_id']);
            if (!$ticket) {
                throw new Exception("Ticket not found: {$payload['ticket_id']}");
            }
            $vars['object_id']   = $ticket->getId();
            $vars['object_type'] = 'T';
        }

        $task = Task::create($vars);
        if (!$task || !$task->getId()) {
            throw new Exception('Task was not created (the requesting staff member needs the task create permission).');
        }
        return ['task_id' => $task->getId(), 'number' => $task->getNumber()];
    }

    public static function edit($payload, $staff_id) {
        global $thisstaff;
        $task = self::requireTask($payload['task_id'] ?? 0);
        self::requirePerm($task, Task::PERM_EDIT, 'edit');

        $changes = [];

        if (isset($payload['title'])) {
            $title = trim((string) $payload['title']);
            if ($title === '') {
                throw new Exception("Task 'title' cannot be empty.");
            }
            if ($errors = FastApiModel::applyAnswers($task, ['title' => $title], 'A')) {
                throw new Exception('Task data rejected: ' . json_encode($errors));
            }
            $changes['title'] = $title;
        }

        if (isset($payload['description'])) {
            $entry = $task->getThread()->entries
                ->filter(['flags__hasbit' => ThreadEntry::FLAG_ORIGINAL_MESSAGE])
                ->order_by('id')->first();
            if ($entry) {
                $entry->setBody((string) $payload['description']);
            } else {
                $errors = [];
                $task->getThread()->addDescription(['description' => (string) $payload['description'], 'staffId' => $thisstaff->getId(), 'poster' => $thisstaff], $errors);
            }
            $changes['description'] = true;
        }

        if (isset($payload['due_date'])) {
            $iform = TaskForm::getInternalForm(['dept_id' => $task->getDeptId(), 'duedate' => (string) $payload['due_date'], 'assignee' => '']);
            if (!$iform->isValid()) {
                throw new Exception('Task data rejected: ' . self::formErrors($iform));
            }
            $due = $iform->getClean()['duedate'];
            $task->duedate = $due ? date('Y-m-d G:i', Misc::dbtime($due)) : null;
            $changes['duedate'] = $due;
        }

        if (isset($payload['dept_id']) && (int) $payload['dept_id'] !== (int) $task->getDeptId()) {
            self::requirePerm($task, Task::PERM_TRANSFER, 'transfer');
            $dept = self::requireDept($payload['dept_id']);
            $errors = [];
            $form = TransferForm::instantiate(['dept' => $dept->getId(), 'comments' => '']);
            if (!$task->transfer($form, $errors, false) || $errors) {
                throw new Exception('Task could not be transferred: ' . json_encode($errors));
            }
        }

        if ($changes) {
            $task->updated = SqlFunction::NOW();
            $task->logEvent('edited', ['fields' => $changes]);
            if (!$task->save()) {
                throw new Exception('Task could not be saved.');
            }
        }
        return ['task_id' => $task->getId()];
    }

    public static function assign($payload, $staff_id) {
        global $thisstaff;
        $task = self::requireTask($payload['task_id'] ?? 0);
        self::requirePerm($task, Task::PERM_ASSIGN, 'assign');

        $assignee = Staff::lookup((int) ($payload['staff_id'] ?? 0));
        if (!$assignee) {
            throw new Exception('Staff not found: ' . ($payload['staff_id'] ?? 'none'));
        }
        $form = AssignmentForm::instantiate(['assignee' => 's' . $assignee->getId(), 'comments' => (string) ($payload['note'] ?? '')]);
        if (!$form->isValid()) {
            throw new Exception('Assignment refused: ' . json_encode($form->errors()));
        }
        $errors = [];
        if (!$task->assign($form, $errors) || $errors) {
            throw new Exception('Task could not be assigned: ' . json_encode($errors ?: 'assignment failed'));
        }
        return ['task_id' => $task->getId(), 'assignee_staff_id' => $assignee->getId()];
    }

    private static function changeStatus($payload, $status, $perm, $what) {
        $task = self::requireTask($payload['task_id'] ?? 0);
        self::requirePerm($task, $perm, $what);
        $errors = [];
        if (!$task->setStatus($status, (string) ($payload['note'] ?? ''), $errors)) {
            throw new Exception($errors
                ? 'Task status not changed: ' . json_encode($errors)
                : "Task is already {$status}.");
        }
        return ['task_id' => $task->getId(), 'status' => $status];
    }

    public static function close($payload, $staff_id) {
        return self::changeStatus($payload, 'closed', Task::PERM_CLOSE, 'close');
    }

    public static function reopen($payload, $staff_id) {
        return self::changeStatus($payload, 'open', Task::PERM_CLOSE, 'reopen');
    }

    public static function addNote($payload, $staff_id) {
        global $thisstaff;
        $task = self::requireTask($payload['task_id'] ?? 0);
        $note = trim((string) ($payload['note'] ?? ''));
        if ($note === '') {
            throw new Exception("A note 'note' is required.");
        }
        $errors = [];
        $entry = $task->postNote(['title' => (string) ($payload['title'] ?? ''), 'note' => $note], $errors, $thisstaff);
        if (!$entry || $errors) {
            throw new Exception('Note could not be posted: ' . json_encode($errors ?: 'post failed'));
        }
        return ['task_id' => $task->getId(), 'entry_id' => $entry->getId()];
    }

    public static function delete($payload, $staff_id) {
        $task = self::requireTask($payload['task_id'] ?? 0);
        self::requirePerm($task, Task::PERM_DELETE, 'delete');
        if (!$task->delete()) {
            throw new Exception('Task could not be deleted.');
        }
        return ['task_id' => (int) ($payload['task_id'] ?? 0)];
    }
}
