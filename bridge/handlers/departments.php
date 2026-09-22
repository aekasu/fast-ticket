<?php
/**
 * Department command handlers.
 *
 * All department mutations are admin-only; this is enforced at the FastAPI
 * layer via require_admin() — the bridge assumes the caller is authorised.
 *
 * Payload field names match app/modules/staff/models.py (DepartmentCreate,
 * DepartmentUpdate).
 */

require_once INCLUDE_DIR . 'class.dept.php';

class FastApiDepartmentHandler {

    private static function requireDept($dept_id) {
        $dept = Dept::lookup((int) $dept_id);
        if (!$dept) {
            throw new Exception("Department not found: {$dept_id}");
        }
        return $dept;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'name'      => $payload['name'] ?? '',
            'ispublic'  => (int) ($payload['is_public'] ?? 1),
            'manager_id'=> $payload['manager_id'] ?? null,
            'email_id'  => $payload['email_id'] ?? null,
            'sla_id'    => $payload['sla_id'] ?? null,
            'signature' => $payload['signature'] ?? '',
        ];
        $errors = [];
        $dept = Dept::create($vars, $errors);
        if (!$dept) {
            throw new Exception('Dept::create failed: ' . implode(', ', $errors));
        }
        return ['dept_id' => $dept->getId(), 'name' => $dept->getName()];
    }

    public static function update($payload, $requester_staff_id) {
        $dept = self::requireDept($payload['dept_id'] ?? 0);
        $vars = [];
        foreach (['name', 'ispublic', 'manager_id', 'email_id', 'sla_id', 'signature'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        // Map is_public → ispublic if caller uses the Python field name
        if (isset($payload['is_public']) && !isset($vars['ispublic'])) {
            $vars['ispublic'] = (int) $payload['is_public'];
        }
        $errors = [];
        if (!$dept->update($vars, $errors)) {
            throw new Exception('Dept::update failed: ' . implode(', ', $errors));
        }
        return ['dept_id' => $dept->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $dept = self::requireDept($payload['dept_id'] ?? 0);
        if (!$dept->delete()) {
            throw new Exception('Dept::delete() failed.');
        }
        return ['dept_id' => (int) ($payload['dept_id'] ?? 0)];
    }
}
