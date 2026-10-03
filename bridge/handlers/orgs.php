<?php
/**
 * Organization command handlers.
 *
 * An organization's name is a column on ost_organization, but address / phone /
 * website / notes are answers of the organization dynamic form (type 'O'), so they are
 * written through FastApiModel::applyAnswers(). The API's `internal_notes` is the
 * form field named `notes`.
 *
 * Profile settings (account manager, email domain, ticket sharing, auto-collaboration flags, primary
 * contacts) are written by applySettings(). Payload field names match app/modules/users/models.py (OrganizationCreate,
 * OrganizationUpdate).
 */

require_once INCLUDE_DIR . 'class.organization.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.team.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiOrgHandler {

    /** API field => dynamic form field name */
    private static $FORM_FIELDS = [
        'address'        => 'address',
        'phone'          => 'phone',
        'website'        => 'website',
        'internal_notes' => 'notes',
    ];

    private static function requireOrg($org_id) {
        $org = Organization::lookup((int) $org_id);
        if (!$org) {
            throw new Exception("Organization not found: {$org_id}");
        }
        return $org;
    }

    private static function formValues(array $payload) {
        $values = [];
        foreach (self::$FORM_FIELDS as $api => $field) {
            // exclude_none on the Python side: an absent key means "leave as is"
            if (array_key_exists($api, $payload) && $payload[$api] !== null) {
                $values[$field] = $payload[$api];
            }
        }
        return $values;
    }

    private static function applyForm(Organization $org, array $payload) {
        $values = self::formValues($payload);
        if ($values && ($errors = FastApiModel::applyAnswers($org, $values, 'O'))) {
            throw new Exception('Organization data rejected: ' . json_encode($errors));
        }
    }

    private static function uniqueName($name, $except_id = 0) {
        $name = trim(Format::striptags((string) $name));
        if ($name === '') {
            throw new Exception("Organization 'name' is required.");
        }
        $other = Organization::lookup(['name' => $name]);
        if ($other && (int) $other->getId() !== (int) $except_id) {
            throw new Exception("An organization named '{$name}' already exists.");
        }
        return $name;
    }

    /** Which of the profile settings the payload carries (anything but the form fields and name). */
    private static function hasSettings(array $payload) {
        foreach (['manager_staff_id', 'manager_team_id', 'domain', 'sharing', 'collab_primary_contacts',
                  'collab_all_members', 'assign_account_manager', 'primary_contact_ids'] as $key) {
            if (array_key_exists($key, $payload)) {
                return true;
            }
        }
        return false;
    }

    /**
     * The profile settings of osTicket's organization page — Organization::update()'s second half, which
     * we cannot call as a whole (it validates the form against a web request). Only keys present in the
     * payload change; the rest stays as stored. The status bits are written through set(), as the model's
     * own protected setStatus() does.
     */
    private static function applySettings(Organization $org, array $payload) {
        if (!self::hasSettings($payload)) {
            return;
        }
        $changed = [];
        $status = (int) $org->status;

        foreach (['collab_all_members' => Organization::COLLAB_ALL_MEMBERS,
                  'collab_primary_contacts' => Organization::COLLAB_PRIMARY_CONTACT,
                  'assign_account_manager' => Organization::ASSIGN_AGENT_MANAGER] as $key => $flag) {
            if (array_key_exists($key, $payload)) {
                $on = (bool) $payload[$key];
                if ((bool) ($status & $flag) !== $on) $changed[] = $key;
                $status = $on ? ($status | $flag) : ($status & ~$flag);
            }
        }
        if (array_key_exists('sharing', $payload)) {
            $share = Organization::SHARE_PRIMARY_CONTACT | Organization::SHARE_EVERYBODY;
            $bit = ['disabled' => 0, 'primary_contacts' => Organization::SHARE_PRIMARY_CONTACT,
                    'all_members' => Organization::SHARE_EVERYBODY][$payload['sharing']] ?? null;
            if ($bit === null) {
                throw new Exception("Unknown sharing mode: {$payload['sharing']}");
            }
            if (($status & $share) !== $bit) $changed[] = 'sharing';
            $status = ($status & ~$share) | $bit;
        }
        if ($status !== (int) $org->status) {
            $org->set('status', $status);
        }

        if (array_key_exists('domain', $payload)) {
            $domain = trim((string) $payload['domain']);
            foreach ($domain === '' ? [] : explode(',', $domain) as $d) {
                if (!Validator::is_email('t@' . trim($d))) {
                    throw new Exception('Enter a valid email domain, like domain.com');
                }
            }
            if ($domain !== (string) $org->domain) $changed[] = 'domain';
            $org->set('domain', $domain);
        }

        if (array_key_exists('manager_staff_id', $payload) || array_key_exists('manager_team_id', $payload)) {
            $staff = (int) ($payload['manager_staff_id'] ?? 0);
            $team = (int) ($payload['manager_team_id'] ?? 0);
            if ($staff && !Staff::lookup($staff)) {
                throw new Exception("Staff not found: {$staff}");
            }
            if ($team && !Team::lookup($team)) {
                throw new Exception("Team not found: {$team}");
            }
            $manager = $staff ? "s{$staff}" : ($team ? "t{$team}" : '');
            if ($manager !== (string) $org->manager) $changed[] = 'manager';
            $org->set('manager', $manager);
        }

        if (array_key_exists('primary_contact_ids', $payload)) {
            $wanted = array_map('intval', (array) $payload['primary_contact_ids']);
            $members = [];
            foreach ($org->allMembers() as $u) {
                $members[(int) $u->id] = $u;
            }
            foreach ($wanted as $user_id) {
                if (!isset($members[$user_id])) {
                    throw new Exception("User {$user_id} is not a member of this organization.");
                }
            }
            foreach ($members as $user_id => $u) {
                $is = in_array($user_id, $wanted, true);
                if ((bool) $u->isPrimaryContact() !== $is) {
                    $u->setPrimaryContact($is);
                    $u->save();
                    $changed[] = 'contacts';
                }
            }
        }

        $org->updated = SqlFunction::NOW();
        if (!$org->save()) {
            throw new Exception('Organization settings could not be saved.');
        }
        foreach (array_unique($changed) as $key) {
            $type = ['type' => 'edited', 'key' => $key];   // Signal::send takes its data by reference
            Signal::send('object.edited', $org, $type);
        }
    }

    public static function create($payload, $staff_id) {
        $name = self::uniqueName($payload['name'] ?? '');

        // fromVars() creates the row (and fires osTicket's created signals); the form data
        // follows, and a rejected value rolls the new organization back.
        $org = Organization::fromVars(['name' => $name]);
        if (!$org || !$org->getId()) {
            throw new Exception('Organization was not created.');
        }
        try {
            self::applyForm($org, $payload);
            self::applySettings($org, $payload);
        } catch (Exception $e) {
            $org->delete();   // don't leave a half-populated organization behind
            throw $e;
        }
        return ['org_id' => $org->getId(), 'name' => $org->name];
    }

    public static function update($payload, $staff_id) {
        $org = self::requireOrg($payload['org_id'] ?? 0);

        if (isset($payload['name'])) {
            $name = self::uniqueName($payload['name'], $org->getId());
        }
        self::applyForm($org, $payload);
        self::applySettings($org, $payload);
        if (isset($name) && $name !== $org->name) {
            $org->name = $name;
            $org->updated = SqlFunction::NOW();
            if (!$org->save()) {
                throw new Exception('Organization name could not be saved.');
            }
        }
        return ['org_id' => $org->getId()];
    }

    public static function delete($payload, $staff_id) {
        $org = self::requireOrg($payload['org_id'] ?? 0);
        if (!$org->delete()) {
            throw new Exception('Organization could not be deleted.');
        }
        return ['org_id' => (int) ($payload['org_id'] ?? 0)];
    }
}
