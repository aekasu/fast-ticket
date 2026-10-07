<?php
/**
 * Roles (Admin Panel -> Agents -> Roles, scp/roles.php): the handler calls Role::update() as that page does, which
 * validates the name (required, unique), requires at least one permission and rewrites the whole permission set —
 * so an update starts from the stored permissions and overlays what the payload sends.
 */
if (!class_exists('FastApiRoleHandler')) {

class FastApiRoleHandler {

    private static function requireRole($role_id) {
        $role = Role::lookup((int) $role_id);
        if (!$role) {
            throw new Exception("Role not found: {$role_id}");
        }
        return $role;
    }

    /** Only keys osTicket's role-permission registry knows. */
    private static function checkKeys(array $keys) {
        $known = [];
        foreach (RolePermission::allPermissions() as $group => $perms) {
            $known = array_merge($known, array_keys($perms));
        }
        foreach ($keys as $key) {
            if (!in_array($key, $known, true)) {
                throw new Exception("Unknown role permission: {$key}");
            }
        }
        return array_values(array_unique($keys));
    }

    private static function errorText(array $errors) {
        return $errors ? implode('; ', array_map('strval', $errors)) : 'unknown error';
    }

    public static function create($payload, $staff_id) {
        $perms = self::checkKeys(array_map('strval', (array) ($payload['permissions'] ?? [])));
        $role = Role::create();
        $errors = [];
        $vars = ['name' => (string) ($payload['name'] ?? ''), 'notes' => (string) ($payload['notes'] ?? ''), 'perms' => $perms];
        if (!$role->update($vars, $errors)) {
            throw new Exception('Role was not created: ' . self::errorText($errors));
        }
        if (!$role->getId()) {
            throw new Exception('Role was not created: nothing was stored.');
        }
        if (array_key_exists('enabled', $payload) && !$payload['enabled']) {
            $role->flags = ((int) $role->flags) & ~Role::FLAG_ENABLED;
            $role->save();
        }
        return ['role_id' => $role->getId(), 'name' => $role->getName()];
    }

    public static function update($payload, $staff_id) {
        $role = self::requireRole($payload['role_id'] ?? 0);
        $stored = array_keys(array_filter((array) $role->getPermissionInfo()));
        $perms = array_key_exists('permissions', $payload)
            ? self::checkKeys(array_map('strval', (array) $payload['permissions']))
            : $stored;
        $errors = [];
        $vars = [
            'id'    => $role->getId(),
            'name'  => array_key_exists('name', $payload) ? (string) $payload['name'] : $role->getName(),
            'notes' => array_key_exists('notes', $payload) ? (string) $payload['notes'] : (string) $role->notes,
            'perms' => $perms,
        ];
        if (!$role->update($vars, $errors)) {
            throw new Exception('Role was not updated: ' . self::errorText($errors));
        }
        if (array_key_exists('enabled', $payload)) {
            $flags = (int) $role->flags;
            $role->flags = $payload['enabled'] ? ($flags | Role::FLAG_ENABLED) : ($flags & ~Role::FLAG_ENABLED);
            $role->save();
        }
        return ['role_id' => $role->getId()];
    }

    public static function delete($payload, $staff_id) {
        $role = self::requireRole($payload['role_id'] ?? 0);
        if (!$role->isDeleteable()) {
            throw new Exception('Role is in use by agents or departments and cannot be deleted.');
        }
        $id = $role->getId();
        if (!$role->delete()) {
            throw new Exception('Role could not be deleted.');
        }
        return ['role_id' => $id];
    }
}
}
