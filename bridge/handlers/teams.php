<?php
/**
 * Team command handlers.
 *
 * All team mutations are admin-only; this is enforced at the FastAPI layer
 * via require_admin() — the bridge assumes the caller is authorised.
 *
 * Payload field names match app/modules/staff/models.py (TeamCreate,
 * TeamUpdate).
 */

require_once INCLUDE_DIR . 'class.team.php';

class FastApiTeamHandler {

    private static function requireTeam($team_id) {
        $team = Team::lookup((int) $team_id);
        if (!$team) {
            throw new Exception("Team not found: {$team_id}");
        }
        return $team;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'name'      => $payload['name'] ?? '',
            'lead_id'   => $payload['lead_id'] ?? null,
            'isenabled' => (int) ($payload['is_enabled'] ?? 1),
            'noalerts'  => (int) ($payload['noalerts'] ?? 0),
        ];
        $errors = [];
        $team = Team::create($vars, $errors);
        if (!$team) {
            throw new Exception('Team::create failed: ' . implode(', ', $errors));
        }
        return ['team_id' => $team->getId(), 'name' => $team->getName()];
    }

    public static function update($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        $vars = [];
        foreach (['name', 'lead_id', 'isenabled', 'noalerts'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        // Map Python-style is_enabled → isenabled
        if (isset($payload['is_enabled']) && !isset($vars['isenabled'])) {
            $vars['isenabled'] = (int) $payload['is_enabled'];
        }
        $errors = [];
        if (!$team->update($vars, $errors)) {
            throw new Exception('Team::update failed: ' . implode(', ', $errors));
        }
        return ['team_id' => $team->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $team = self::requireTeam($payload['team_id'] ?? 0);
        if (!$team->delete()) {
            throw new Exception('Team::delete() failed.');
        }
        return ['team_id' => (int) ($payload['team_id'] ?? 0)];
    }
}
