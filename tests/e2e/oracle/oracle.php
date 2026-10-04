<?php
/**
 * The oracle: osTicket's own answer to "what may this agent see?", for comparison with the API.
 *
 * Usage (inside the osTicket container):  php oracle.php '<json>'
 *   {"staff_ids": [..], "ticket_ids": [..], "task_ids": [..], "canned_ids": [..], "perm_keys": [..]}
 *
 * Prints ORACLE_JSON_BEGIN <json> ORACLE_JSON_END. Everything below calls osTicket's own methods (or, where
 * osTicket puts the rule inline in a page rather than a method, copies that page's code line for line), so
 * the API is being compared with osTicket itself, not with a description of it.
 *
 * Read-only: it never writes to osTicket.
 */
error_reporting(E_ALL & ~E_DEPRECATED & ~E_STRICT);
ini_set('display_errors', '0');
register_shutdown_function(function () {   // a fatal error would otherwise leave no output at all
    if (($e = error_get_last()) && in_array($e['type'], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR], true))
        echo 'ORACLE_FATAL (staff ' . ($GLOBALS['current_staff'] ?? '?') . ') ' . $e['message'];
});
chdir('/var/www/html/api');
define('OSTAPI', true);
require 'api.inc.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.ticket.php';
require_once INCLUDE_DIR . 'class.task.php';
require_once INCLUDE_DIR . 'class.canned.php';
require_once INCLUDE_DIR . 'class.topic.php';

$in = json_decode($argv[1] ?? '{}', true);
$ticket_ids = array_map('intval', $in['ticket_ids'] ?? []);
$task_ids   = array_map('intval', $in['task_ids'] ?? []);
$canned_ids = array_map('intval', $in['canned_ids'] ?? []);
$perm_keys  = $in['perm_keys'] ?? [];

$ints = function ($values) { $v = array_map('intval', (array) $values); sort($v); return array_values(array_unique($v)); };
$out = [];

foreach ($in['staff_ids'] ?? [] as $sid) {
    global $thisstaff;
    $GLOBALS['current_staff'] = $sid;
    $staff = Staff::lookup((int) $sid);
    $thisstaff = $staff;                 // Canned::getCannedResponses() reads the global
    $row = [];

    // ── who they are ──────────────────────────────────────────────────────
    $row['is_admin'] = (bool) $staff->isAdmin();
    $row['assigned_only'] = (bool) $staff->isAccessLimited();
    $row['depts'] = $ints($staff->getDepts());
    $row['teams'] = $ints($staff->getTeams());
    $row['global_perms'] = []; foreach ($perm_keys as $k) if ($staff->hasPerm($k)) $row['global_perms'][] = $k;
    $row['roles'] = [];
    foreach ($staff->getRoles() as $dept => $role) {
        if (!$role) throw new Exception("staff {$staff->username} has no role in department $dept (primary role_id {$staff->role_id}, dept {$staff->dept_id})");
        $has = []; foreach ($perm_keys as $k) if ($role->hasPerm($k)) $has[] = $k;
        $row['roles'][(string) $dept] = $has;
    }
    ksort($row['roles']);

    // ── tickets: the list (Staff::getTicketsVisibility) and the page (Ticket::checkStaffPerm) ──
    $list = [];
    if ($ticket_ids) {
        $q = Ticket::objects()->filter(['ticket_id__in' => $ticket_ids])->filter($staff->getTicketsVisibility());
        foreach ($q->values_flat('ticket_id') as $r) $list[] = (int) $r[0];
    }
    $row['tickets_list'] = $ints($list);
    $page = [];
    foreach ($ticket_ids as $id)
        if (($t = Ticket::lookup($id)) && $t->checkStaffPerm($staff)) $page[] = $id;
    $row['tickets_page'] = $ints($page);

    // ── tasks: the visibility block of include/staff/tasks.inc.php, and Task::checkStaffPerm ──
    $list = [];
    if ($task_ids) {
        $visibility = Q::any(new Q(['flags__hasbit' => TaskModel::ISOPEN, 'staff_id' => $staff->getId()]));
        $visibility->add(new Q(['ticket__staff_id' => $staff->getId(), 'ticket__status__state' => 'open']));
        if (!$staff->showAssignedOnly() && ($depts = $staff->getDepts()))
            $visibility->add(new Q(['dept_id__in' => $depts]));
        if (($teams = $staff->getTeams()) && count(array_filter($teams)))
            $visibility->add(new Q(['team_id__in' => array_filter($teams), 'flags__hasbit' => TaskModel::ISOPEN]));
        $tasks = Task::objects()->filter(['id__in' => $task_ids])->filter(new Q($visibility));
        foreach ($tasks->values_flat('id') as $r) $list[] = (int) $r[0];
    }
    $row['tasks_list'] = $ints($list);
    $page = [];
    foreach ($task_ids as $id)
        if (($t = Task::lookup($id)) && $t->checkStaffPerm($staff)) $page[] = $id;
    $row['tasks_page'] = $ints($page);

    // ── reference lists ───────────────────────────────────────────────────
    $row['department_names'] = $ints(array_keys($staff->getDepartmentNames()));
    $topics = [];
    foreach ($staff->getTopicNames() as $id => $name) $topics[(string) $id] = $name;
    $row['topic_names'] = $topics;

    // ── canned responses: the reply picker, and the management page's own code ──
    $picker = array_keys(Canned::getCannedResponses());
    $row['canned_picker'] = $ints(array_intersect($picker, $canned_ids));

    $departments = [];
    if ($staff->getRole()->hasPerm(Canned::PERM_MANAGE, false))
        $departments[] = $staff->getDeptId();
    foreach ($staff->dept_access as $da)
        if ($da->role->hasPerm(Canned::PERM_MANAGE, false))
            $departments[] = $da->dept->getId();
    $managed = [];
    if ($canned_ids) {
        // include/staff/cannedresponses.inc.php: WHERE canned.dept_id = 0 OR dept.id IN (departments)
        $in_list = $departments ? implode(',', array_map('intval', $departments)) : '0';
        $res = db_query('SELECT canned.canned_id FROM ' . CANNED_TABLE . ' canned LEFT JOIN ' . DEPT_TABLE
            . ' dept ON (dept.id=canned.dept_id) WHERE canned.canned_id IN (' . implode(',', $canned_ids)
            . ') AND (canned.dept_id = 0 OR dept.id IN (' . $in_list . '))');
        while ($res && ($r = db_fetch_row($res))) $managed[] = (int) $r[0];
    }
    $row['canned_manage'] = $ints($managed);

    // (last: getDeptAgents() joins dept_access and leaves partial staff objects in osTicket's ORM cache)
    // ── agent directory (scp/directory.php: $thisstaff->getDeptAgents()) ───────────────────────────
    $row['directory'] = [];
    foreach ($staff->getDeptAgents() as $agent) $row['directory'][] = (int) $agent->getId();
    $row['directory'] = $ints($row['directory']);

    // ── assign form: may they assign (Ticket::checkStaffPerm), and who is offered (Dept::getAssignees) ──
    $row['assign_ok'] = [];
    $row['assignees'] = [];
    foreach ($ticket_ids as $id) {
        $t = Ticket::lookup($id);
        if (!$t || !$t->checkStaffPerm($staff, Ticket::PERM_ASSIGN)) continue;
        $row['assign_ok'][] = $id;
        $agents = [];
        foreach ($t->getDept()->getAssignees(['staff' => $staff]) as $agent) $agents[] = (int) $agent->getId();
        $row['assignees'][(string) $id] = $ints($agents);
    }
    $row['assign_ok'] = $ints($row['assign_ok']);
    $teams = [];
    foreach (Team::getActiveTeams() ?: [] as $id => $name) $teams[] = (int) $id;
    $row['assignable_teams'] = $ints($teams);

    ModelInstanceManager::flushCache();   // drop those partial objects before the next agent is loaded
    $out[(string) $sid] = $row;
}
echo "ORACLE_JSON_BEGIN" . json_encode($out) . "ORACLE_JSON_END";
