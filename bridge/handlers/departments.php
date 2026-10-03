<?php
/**
 * Department command handlers.
 *
 * All department mutations are admin-only; this is enforced at the FastAPI
 * layer via require_admin() — the bridge assumes the caller is authorised.
 *
 * Payload field names match app/modules/staff/models.py (DepartmentCreate,
 * DepartmentUpdate).
 *
 * osTicket's Dept::create() only instantiates; Dept::update($vars, &$errors)
 * validates and saves, and it OVERWRITES every field from $vars — including
 * dropping all extended staff access when `members` is absent. So updates start
 * from the stored state (storedVars) and overlay only what the payload provides.
 *
 * Fields mirror osTicket's department page: parent_id, sla_id, schedule_id, template_id,
 * autoresponse_email_id, signature, status (active|disabled|archived), assignment
 * (all|members|primary), disable_auto_claim, disable_reopen_auto_assign, alerts_to
 * (dept_only|dept_and_extended|no_one|admin_only), ticket_auto_response, message_auto_response
 * and members (extended access: staff_id, role_id, alerts; replaces the set). */

require_once INCLUDE_DIR . 'class.dept.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.role.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiDepartmentHandler {

    private static function requireDept($dept_id) {
        $dept = Dept::lookup((int) $dept_id);
        if (!$dept) {
            throw new Exception("Department not found: {$dept_id}");
        }
        return $dept;
    }

    /** The vars Dept::update() would need to leave $dept exactly as it is. */
    private static function storedVars(Dept $dept) {
        $flags = (int) $dept->flags;
        $vars = [
            'id'                   => $dept->getId(),
            'name'                 => $dept->name,
            'pid'                  => $dept->pid,
            'ispublic'             => (int) $dept->ispublic,
            'manager_id'           => $dept->manager_id,
            'email_id'             => $dept->email_id,
            'tpl_id'               => $dept->tpl_id,
            'sla_id'               => $dept->sla_id,
            'schedule_id'          => $dept->schedule_id,
            'autoresp_email_id'    => $dept->autoresp_email_id,
            'signature'            => $dept->signature,
            'group_membership'     => $dept->group_membership,
            'ticket_auto_response' => $dept->ticket_auto_response,
            'message_auto_response'=> $dept->message_auto_response,
            'flags'                => 0, // update() rebuilds every bit below from these keys
            'status'               => ($flags & Dept::FLAG_ARCHIVED) ? 'archived'
                                       : (($flags & Dept::FLAG_ACTIVE) ? 'active' : 'disabled'),
            'assignment_flag'      => ($flags & Dept::FLAG_ASSIGN_PRIMARY_ONLY) ? 'primary'
                                       : (($flags & Dept::FLAG_ASSIGN_MEMBERS_ONLY) ? 'members' : 'all'),
        ];
        // update() reads these three with isset(): present = on.
        if ($flags & Dept::FLAG_ASSIGN_MEMBERS_ONLY)        $vars['assign_members_only'] = 1;
        if ($flags & Dept::FLAG_DISABLE_AUTO_CLAIM)         $vars['disable_auto_claim'] = 1;
        if ($flags & Dept::FLAG_DISABLE_REOPEN_AUTO_ASSIGN) $vars['disable_reopen_auto_assign'] = 1;

        // Without `members`, updateAccess() deletes every extended-access row.
        $vars['members'] = $vars['member_role'] = $vars['member_alerts'] = [];
        foreach ($dept->extended as $access) {
            $vars['members'][] = $access->staff_id;
            $vars['member_role'][$access->staff_id] = $access->role_id;
            $vars['member_alerts'][$access->staff_id] = $access->isAlertsEnabled() ? 1 : 0;
        }
        return $vars;
    }

    private static function defaultVars() {
        return [
            'name' => '', 'pid' => null, 'ispublic' => 1, 'manager_id' => 0, 'email_id' => 0,
            'tpl_id' => 0, 'sla_id' => 0, 'schedule_id' => 0, 'autoresp_email_id' => 0,
            'signature' => '', 'group_membership' => 0,
            'ticket_auto_response' => 1, 'message_auto_response' => 1,
            'flags' => 0, 'status' => 'active', 'assignment_flag' => 'all',
        ];
    }

    /** API field => Dept::update() key, for the plain scalars. */
    private static $scalars = [
        'name' => 'name', 'signature' => 'signature', 'manager_id' => 'manager_id', 'email_id' => 'email_id',
        'sla_id' => 'sla_id', 'schedule_id' => 'schedule_id', 'template_id' => 'tpl_id',
        'autoresponse_email_id' => 'autoresp_email_id',
    ];
    private static $alerts = ['dept_only' => Dept::ALERTS_DEPT_ONLY, 'dept_and_extended' => Dept::ALERTS_DEPT_AND_EXTENDED,
                              'no_one' => Dept::ALERTS_DISABLED, 'admin_only' => Dept::ALERTS_ADMIN_ONLY];

    private static function overlay(array $vars, array $payload) {
        foreach (self::$scalars as $field => $key) {
            if (isset($payload[$field])) {
                $vars[$key] = $payload[$field];
            }
        }
        if (isset($payload['parent_id'])) {
            $vars['pid'] = $payload['parent_id'] ?: null;
        }
        if (isset($payload['is_public'])) {
            $vars['ispublic'] = (int) $payload['is_public'];
        }
        if (isset($payload['status'])) {
            $vars['status'] = $payload['status'];
        }
        if (isset($payload['assignment'])) {
            $vars['assignment_flag'] = $payload['assignment'];
        }
        // checkboxes: present = on
        foreach (['disable_auto_claim', 'disable_reopen_auto_assign'] as $key) {
            if (isset($payload[$key])) {
                if ($payload[$key]) $vars[$key] = 1; else unset($vars[$key]);
            }
        }
        if (isset($payload['alerts_to'])) {
            $vars['group_membership'] = self::$alerts[$payload['alerts_to']];
        }
        foreach (['ticket_auto_response', 'message_auto_response'] as $key) {
            if (isset($payload[$key])) {
                $vars[$key] = (int) $payload[$key];
            }
        }
        // Extended access replaces the whole set; update() drops whoever is not listed.
        if (isset($payload['members'])) {
            $vars['members'] = $vars['member_role'] = $vars['member_alerts'] = [];
            foreach ((array) $payload['members'] as $row) {
                $staff_id = (int) ($row['staff_id'] ?? 0);
                $role_id = (int) ($row['role_id'] ?? 0);
                if (!Staff::lookup($staff_id)) {
                    throw new Exception("Staff not found: {$staff_id}");
                }
                if (!Role::lookup($role_id)) {
                    throw new Exception("Role not found: {$role_id}");
                }
                $vars['members'][] = $staff_id;
                $vars['member_role'][$staff_id] = $role_id;
                $vars['member_alerts'][$staff_id] = !empty($row['alerts']) ? 1 : 0;
            }
        }
        return $vars;
    }

    /**
     * A new extended-access row keeps the column default (alerts on): the ORM never writes a false
     * to a field it saw as unset. Re-read the rows and switch alerts off where asked.
     */
    private static function applyAlertFlags(Dept $dept, array $vars) {
        foreach ((array) ($vars['members'] ?? []) as $staff_id) {
            if (!empty($vars['member_alerts'][$staff_id])) {
                continue;
            }
            $row = StaffDeptAccess::objects()->filter(['dept_id' => $dept->getId(), 'staff_id' => $staff_id])->first();
            if ($row && $row->isAlertsEnabled()) {
                $row->setAlerts(false);
                $row->save();
            }
        }
    }

    private static function save(Dept $dept, array $vars) {
        FastApiModel::saveViaUpdate($dept, $vars, 'Department');
        self::applyAlertFlags($dept, $vars);
    }

    public static function create($payload, $requester_staff_id) {
        $dept = Dept::create();
        self::save($dept, self::overlay(self::defaultVars(), $payload));
        return ['dept_id' => $dept->getId(), 'name' => $dept->name];
    }

    public static function update($payload, $requester_staff_id) {
        $dept = self::requireDept($payload['dept_id'] ?? 0);
        self::save($dept, self::overlay(self::storedVars($dept), $payload));
        return ['dept_id' => $dept->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $dept = self::requireDept($payload['dept_id'] ?? 0);
        if (!$dept->delete()) {
            throw new Exception('Department could not be deleted (it may be the default department, or still have staff or tickets).');
        }
        return ['dept_id' => (int) ($payload['dept_id'] ?? 0)];
    }
}
