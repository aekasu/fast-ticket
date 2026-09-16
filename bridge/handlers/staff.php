<?php
/**
 * Staff command handlers.
 *
 * All staff mutations are admin-only; this is enforced at the FastAPI layer
 * via require_admin() — the bridge assumes the caller is already authorised.
 */

require_once INCLUDE_DIR . 'class.staff.php';

class FastApiStaffHandler {

    private static function requireStaff($staff_id) {
        $staff = Staff::lookup((int) $staff_id);
        if (!$staff) {
            throw new Exception("Staff not found: {$staff_id}");
        }
        return $staff;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'firstname' => $payload['firstname'] ?? '',
            'lastname'  => $payload['lastname'] ?? '',
            'email'     => $payload['email'] ?? '',
            'username'  => $payload['username'] ?? '',
            'passwd'    => $payload['password'] ?? bin2hex(random_bytes(16)),
            'isactive'  => 1,
            'isadmin'   => (int) ($payload['is_admin'] ?? 0),
            'dept_id'   => $payload['dept_id'] ?? null,
            'role_id'   => $payload['role_id'] ?? null,
        ];
        $errors = [];
        $staff  = Staff::create($vars, $errors);
        if (!$staff) {
            throw new Exception('Staff::create failed: ' . implode(', ', $errors));
        }
        return [
            'staff_id' => $staff->getId(),
            'username' => $staff->getUsername(),
        ];
    }

    public static function update($payload, $requester_staff_id) {
        $staff = self::requireStaff($payload['staff_id'] ?? 0);
        $vars  = [];
        foreach (['firstname', 'lastname', 'email', 'username', 'isactive', 'dept_id', 'role_id'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        $errors = [];
        if (!$staff->update($vars, $errors)) {
            throw new Exception('Staff::update failed: ' . implode(', ', $errors));
        }
        return ['staff_id' => $staff->getId()];
    }
}
