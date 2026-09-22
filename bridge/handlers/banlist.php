<?php
/**
 * Banlist command handlers.
 *
 * Manage the email banlist (reject list) in osTicket.
 * Payload field names match app/modules/banlist/models.py (BanlistCreate,
 * BanlistUpdate).
 */

require_once INCLUDE_DIR . 'class.banlist.php';

class FastApiBanlistHandler {

    private static function requireBanEntry($ban_id) {
        $entry = Banlist::lookup((int) $ban_id);
        if (!$entry) {
            throw new Exception("Banlist entry not found: {$ban_id}");
        }
        return $entry;
    }

    public static function create($payload, $requester_staff_id) {
        $val = trim($payload['val'] ?? '');
        if ($val === '') {
            throw new Exception('Banlist::create requires a non-empty val (email/domain).');
        }
        $vars = [
            'val'      => $val,
            'isactive' => (int) ($payload['is_active'] ?? 1),
        ];
        $errors = [];
        $entry = Banlist::create($vars, $errors);
        if (!$entry) {
            throw new Exception('Banlist::create failed: ' . implode(', ', $errors));
        }
        return ['ban_id' => $entry->getId(), 'val' => $entry->getVal()];
    }

    public static function update($payload, $requester_staff_id) {
        $entry = self::requireBanEntry($payload['ban_id'] ?? 0);
        $vars  = [];
        if (isset($payload['val'])) {
            $vars['val'] = trim($payload['val']);
        }
        if (isset($payload['is_active'])) {
            $vars['isactive'] = (int) $payload['is_active'];
        }
        $errors = [];
        if (!$entry->update($vars, $errors)) {
            throw new Exception('Banlist::update failed: ' . implode(', ', $errors));
        }
        return ['ban_id' => $entry->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $entry = self::requireBanEntry($payload['ban_id'] ?? 0);
        if (!$entry->delete()) {
            throw new Exception('Banlist::delete() failed.');
        }
        return ['ban_id' => (int) ($payload['ban_id'] ?? 0)];
    }
}
