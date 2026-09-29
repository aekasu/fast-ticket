<?php
/**
 * Banlist command handlers.
 *
 * osTicket has no Banlist model: the ban list is the "SYSTEM BAN LIST" ticket
 * Filter, and each banned address is one of its FilterRule rows (what='email',
 * how='equal'). This mirrors scp/banlist.php. `ban_id` is the FilterRule id.
 *
 * Payload field names match app/modules/banlist/models.py (BanlistCreate,
 * BanlistUpdate).
 */

require_once INCLUDE_DIR . 'class.banlist.php';
require_once INCLUDE_DIR . 'class.filter.php';

class FastApiBanlistHandler {

    private static function systemFilter() {
        $filter = Banlist::getFilter();
        if (!$filter) {
            Banlist::createSystemBanList();
            $filter = Banlist::getFilter();
        }
        if (!$filter) {
            throw new Exception('System ban list filter is missing and could not be created.');
        }
        return $filter;
    }

    private static function requireRule($filter, $ban_id) {
        $rule = $filter->getRule((int) $ban_id);
        if (!$rule) {
            throw new Exception("Banlist entry not found: {$ban_id}");
        }
        return $rule;
    }

    private static function requireEmail($val) {
        if ($val === '' || !Validator::is_email($val)) {
            throw new Exception('A valid email address is required.');
        }
    }

    public static function create($payload, $requester_staff_id) {
        $val = trim($payload['val'] ?? '');
        self::requireEmail($val);
        if (Banlist::includes($val)) {
            throw new Exception("Email already in the ban list: {$val}");
        }

        $filter = self::systemFilter();
        $extra = [
            'isactive' => (int) ($payload['is_active'] ?? 1),
            'notes'    => $payload['notes'] ?? '',
        ];
        // Filter::addRule() returns true, not the rule, so look it up for its id.
        if (!$filter->addRule('email', 'equal', $val, $extra)) {
            throw new Exception("Unable to add {$val} to the ban list.");
        }
        $rule = FilterRule::lookup(['filter_id' => $filter->getId(), 'what' => 'email', 'how' => 'equal', 'val' => $val]);
        if (!$rule) {
            throw new Exception("Ban rule for {$val} was not persisted.");
        }
        return ['ban_id' => $rule->getId(), 'val' => $val];
    }

    public static function update($payload, $requester_staff_id) {
        $filter = self::systemFilter();
        $rule   = self::requireRule($filter, $payload['ban_id'] ?? 0);

        // FilterRule::update() overwrites every field; the API sends partial updates.
        $vars = [
            'what'      => 'email',
            'how'       => 'equal',
            'val'       => isset($payload['val']) ? trim($payload['val']) : $rule->val,
            'filter_id' => $filter->getId(),
            'isactive'  => isset($payload['is_active']) ? (int) $payload['is_active'] : $rule->isactive,
            'notes'     => isset($payload['notes']) ? $payload['notes'] : $rule->notes,
        ];
        self::requireEmail($vars['val']);

        $errors = [];
        if (!$rule->update($vars, $errors) || $errors) {
            throw new Exception('Unable to update ban rule: ' . json_encode($errors ?: 'save failed'));
        }
        return ['ban_id' => $rule->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $filter = self::systemFilter();
        $rule   = self::requireRule($filter, $payload['ban_id'] ?? 0);
        if (!$rule->delete()) {
            throw new Exception('Unable to delete ban rule.');
        }
        return ['ban_id' => (int) ($payload['ban_id'] ?? 0)];
    }
}
