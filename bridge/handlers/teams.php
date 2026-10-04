<?php
/**
 * Team command handlers.
 *
 * All team mutations are admin-only; this is enforced at the FastAPI layer
 * via require_admin() — the bridge assumes the caller is authorised.
 *
 * Payload field names match app/modules/staff/models.py (TeamCreate,
 * TeamUpdate).
 *
 * osTicket's Team::create() only instantiates; Team::update($vars, &$errors)
 * validates and saves, overwrites every field from $vars, and REMOVES every
 * member when `members` is absent. So updates start from the stored state
 * (storedVars) and overlay only what the payload provides.
 */

require_once INCLUDE_DIR . 'class.team.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiTeamHandler {

    private static function requireTeam($team_id) {
        $team = Team::lookup((int) $team_id);
        if (!$team) {
            throw new Exception("Team not found: {$team_id}");
        }
        return $team;
    }

    /** The vars Team::update() would need to leave $team exactly as it is. */
    private static function storedVars(Team $team) {
        $flags = (int) $team->flags;
        $vars = [
            'id'        => $team->getId(),
            'name'      => $team->name,
            'isenabled' => ($flags & Team::FLAG_ENABLED) ? 1 : 0,
            'lead_id'   => $team->lead_id,
            'notes'     => $team->notes,
            'members'   => [],
            'member_alerts' => [],
        ];
        if ($flags & Team::FLAG_NOALERTS) {
            $vars['noalerts'] = 1; // read with isset(): present = on
        }
        foreach ($team->members as $member) {
            $vars['members'][] = $member->staff_id;
            $vars['member_alerts'][$member->staff_id] = $member->isAlertsEnabled() ? 1 : 0;
        }
        return $vars;
    }

    private static function overlay(array $vars, array $payload) {
        foreach (['name', 'lead_id', 'notes'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (isset($payload['is_enabled'])) {
            $vars['isenabled'] = (int) $payload['is_enabled'];
        }
        if (isset($payload['no_alerts'])) {
            if ($payload['no_alerts']) {
                $vars['noalerts'] = 1;
            } else {
                unset($vars['noalerts']);
            }
        }
        if (isset($payload['members'])) {
            // Keep the alert preference of members that stay; new members get alerts on.
            $previous = $vars['member_alerts'];
            $vars['members'] = array_values(array_map('intval', (array) $payload['members']));
            $vars['member_alerts'] = [];
            foreach ($vars['members'] as $staff_id) {
                $vars['member_alerts'][$staff_id] = $previous[$staff_id] ?? 1;
            }
        }
        // Per-member alert preference: only for people who are (or are becoming) members.
        foreach ((array) ($payload['member_alerts'] ?? []) as $staff_id => $on) {
            if (!in_array((int) $staff_id, $vars['members'], true)) {
                throw new Exception("Staff {$staff_id} is not a member of the team; add them to 'members' first.");
            }
            $vars['member_alerts'][(int) $staff_id] = $on ? 1 : 0;
        }
        return $vars;
    }

    private static function save(Team $team, array $vars) {
        FastApiModel::saveViaUpdate($team, $vars, 'Team');
    }

    public static function create($payload, $requester_staff_id) {
        $defaults = ['name' => '', 'isenabled' => 1, 'lead_id' => 0, 'notes' => '', 'members' => [], 'member_alerts' => []];
        $team = Team::create();
        self::save($team, self::overlay($defaults, $payload));
        return ['team_id' => $team->getId(), 'name' => $team->name];
    }

    public static function update($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        self::save($team, self::overlay(self::storedVars($team), $payload));
        return ['team_id' => $team->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        if (!$team->delete()) {
            throw new Exception('Team could not be deleted.');
        }
        return ['team_id' => (int) ($payload['team_id'] ?? 0)];
    }
}
