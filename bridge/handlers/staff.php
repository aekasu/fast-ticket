<?php
/**
 * Staff command handlers.
 *
 * All staff mutations are admin-only; this is enforced at the FastAPI layer
 * via require_admin() — the bridge assumes the caller is already authorised.
 *
 * Payload field names match app/modules/staff/models.py (StaffCreate, StaffUpdate).
 *
 * Staff::update($vars, &$errors) is written for a web form: it OVERWRITES every field
 * from $vars, reads booleans as checkboxes (a key that is present means "on", and
 * `islocked` present means the account is *inactive*), and wipes permissions, team
 * membership and extended department access when their keys are absent. So updates
 * start from the stored state (storedVars) and overlay only what the payload provides.
 *
 * `isadmin` must be the string '1': update() compares it with !==.
 */

require_once INCLUDE_DIR . 'class.staff.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiStaffHandler {

    private static function requireStaff($staff_id) {
        $staff = Staff::lookup((int) $staff_id);
        if (!$staff) {
            throw new Exception("Staff not found: {$staff_id}");
        }
        return $staff;
    }

    /** The vars Staff::update() would need to leave $staff exactly as it is. */
    private static function storedVars(Staff $staff) {
        $vars = [
            'id'        => $staff->getId(),
            'username'  => $staff->username,
            'firstname' => $staff->firstname,
            'lastname'  => $staff->lastname,
            'email'     => $staff->email,
            'phone'     => $staff->phone,
            'phone_ext' => $staff->phone_ext,
            'mobile'    => $staff->mobile,
            'notes'     => $staff->notes,
            'dept_id'   => $staff->dept_id,
            'role_id'   => $staff->role_id,
            'backend'   => $staff->backend,
            // update() rewrites the permission JSON from this list ('' when empty)
            'perms'     => array_keys(array_filter((array) $staff->getPermissionInfo())),
            'teams' => [], 'team_alerts' => [],
            'dept_access' => [], 'dept_access_role' => [], 'dept_access_alerts' => [],
        ];
        if ($staff->isadmin)        $vars['isadmin'] = '1';
        if (!$staff->isactive)      $vars['islocked'] = '1';
        if ($staff->isvisible)      $vars['isvisible'] = 1;
        if ($staff->onvacation)     $vars['onvacation'] = 1;
        if ($staff->assigned_only)  $vars['assigned_only'] = 1;
        if ($staff->getExtraAttr('def_assn_role')) $vars['assign_use_pri_role'] = 1;

        foreach ($staff->teams as $member) {
            $vars['teams'][] = $member->team_id;
            $vars['team_alerts'][$member->team_id] = $member->isAlertsEnabled() ? 1 : 0;
        }
        foreach ($staff->dept_access as $access) {
            $vars['dept_access'][] = $access->dept_id;
            $vars['dept_access_role'][$access->dept_id] = $access->role_id;
            $vars['dept_access_alerts'][$access->dept_id] = $access->isAlertsEnabled() ? 1 : 0;
        }
        return $vars;
    }

    private static function defaultVars() {
        return [
            'username' => '', 'firstname' => '', 'lastname' => '', 'email' => '', 'phone' => '',
            'phone_ext' => '', 'mobile' => '', 'notes' => '', 'dept_id' => 0, 'role_id' => 0, 'backend' => null,
            'perms' => [], 'isvisible' => 1,
            'teams' => [], 'team_alerts' => [],
            'dept_access' => [], 'dept_access_role' => [], 'dept_access_alerts' => [],
        ];
    }

    private static function overlay(array $vars, array $payload) {
        foreach (['username', 'firstname', 'lastname', 'email', 'phone', 'mobile', 'dept_id', 'role_id'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (isset($payload['is_admin'])) {
            if ($payload['is_admin']) $vars['isadmin'] = '1'; else unset($vars['isadmin']);
        }
        if (isset($payload['is_active'])) {
            if ($payload['is_active']) unset($vars['islocked']); else $vars['islocked'] = '1';
        }
        if (!empty($payload['password'])) {
            $vars['passwd1'] = $payload['password'];   // update() calls setPassword() with it
        }
        if (isset($payload['teams'])) {
            $previous = $vars['team_alerts'];
            $vars['teams'] = array_values(array_map('intval', (array) $payload['teams']));
            $vars['team_alerts'] = [];
            foreach ($vars['teams'] as $team_id) {
                $vars['team_alerts'][$team_id] = $previous[$team_id] ?? 1;
            }
        }
        return $vars;
    }

    private static function save(Staff $staff, array $vars) {
        FastApiModel::saveViaUpdate($staff, $vars, 'Staff member');
    }

    public static function create($payload, $requester_staff_id) {
        if (empty($payload['password'])) {
            throw new Exception('A password is required to create a staff member.');
        }
        $staff = Staff::create();
        $vars = self::overlay(self::defaultVars(), $payload);
        if (!isset($payload['is_active']) || $payload['is_active']) {
            unset($vars['islocked']);
        }
        self::save($staff, $vars);
        return ['staff_id' => $staff->getId(), 'username' => $staff->username];
    }

    public static function update($payload, $requester_staff_id) {
        $staff = self::requireStaff($payload['staff_id'] ?? 0);
        self::save($staff, self::overlay(self::storedVars($staff), $payload));
        return ['staff_id' => $staff->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $staff = self::requireStaff($payload['staff_id'] ?? 0);
        // Staff::delete() also refuses when the target is the acting staff member.
        if (!$staff->delete()) {
            throw new Exception('Staff member could not be deleted (an agent cannot delete themselves).');
        }
        return ['staff_id' => (int) ($payload['staff_id'] ?? 0)];
    }
}
