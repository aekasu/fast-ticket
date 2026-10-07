<?php
/**
 * osTicket's API keys (scp/apikeys.php) through its own API::save(): validates the address, generates the key, and
 * rewrites every flag it knows — so an update starts from the stored values.
 */
include_once INCLUDE_DIR . 'class.api.php';

if (!class_exists('FastApiApiKeyHandler')) {

class FastApiApiKeyHandler {

    private static function requireKey($key_id) {
        $key = API::lookup((int) $key_id);
        if (!$key) {
            throw new Exception("API key not found: {$key_id}");
        }
        return $key;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function create($payload, $staff_id) {
        $errors = [];
        $vars = [
            'ipaddr'             => trim((string) ($payload['ip_address'] ?? '')),
            'isactive'           => (int) !empty($payload['is_active']),
            'can_create_tickets' => (int) !empty($payload['can_create_tickets']),
            'can_exec_cron'      => (int) !empty($payload['can_exec_cron']),
            'notes'              => (string) ($payload['notes'] ?? ''),
        ];
        $id = API::add($vars, $errors);
        if (!$id || $errors) {
            throw new Exception('API key was not created: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['key_id' => (int) $id];
    }

    public static function update($payload, $staff_id) {
        $key = self::requireKey($payload['key_id'] ?? 0);
        $flag = function ($name, $stored) use ($payload) {
            return (int) (array_key_exists($name, $payload) ? !empty($payload[$name]) : $stored);
        };
        $vars = [
            'isactive'           => $flag('is_active', $key->isActive()),
            'can_create_tickets' => $flag('can_create_tickets', $key->canCreateTickets()),
            'can_exec_cron'      => $flag('can_exec_cron', $key->canExecuteCron()),
            'notes'              => array_key_exists('notes', $payload) ? (string) $payload['notes'] : (string) $key->getNotes(),
        ];
        $errors = [];
        if (!$key->update($vars, $errors) || $errors) {
            throw new Exception('API key was not updated: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['key_id' => $key->getId()];
    }

    public static function delete($payload, $staff_id) {
        $key = self::requireKey($payload['key_id'] ?? 0);
        $id = $key->getId();
        if (!$key->delete()) {
            throw new Exception('API key could not be deleted.');
        }
        return ['key_id' => $id];
    }
}
}
