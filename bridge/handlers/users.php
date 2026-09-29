<?php
/**
 * User command handlers.
 *
 * A user's name and email are model columns / a UserEmail row; phone and notes are
 * answers of the user dynamic form (type 'U'), written through
 * FastApiModel::applyAnswers(). The API's `internal_notes` is the form field `notes`.
 * osTicket's default user form has no mobile field, so `mobile` is rejected unless an
 * administrator has added a form field with that name.
 *
 * Organization membership is the `org_id` column: set it (or 0 to clear) on create or
 * update.
 *
 * Payload field names match app/modules/users/models.py (UserCreate, UserUpdate).
 */

require_once INCLUDE_DIR . 'class.user.php';
require_once INCLUDE_DIR . 'class.organization.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiUserHandler {

    /** API field => user form field name */
    private static $FORM_FIELDS = [
        'phone'          => 'phone',
        'mobile'         => 'mobile',
        'internal_notes' => 'notes',
    ];

    private static function requireUser($user_id) {
        $user = User::lookup((int) $user_id);
        if (!$user) {
            throw new Exception("User not found: {$user_id}");
        }
        return $user;
    }

    private static function requireOrg($org_id) {
        $org = Organization::lookup((int) $org_id);
        if (!$org) {
            throw new Exception("Organization not found: {$org_id}");
        }
        return $org;
    }

    private static function requireEmail($email) {
        $email = trim((string) $email);
        if (!Validator::is_email($email)) {
            throw new Exception("A valid email address is required (got '{$email}').");
        }
        return $email;
    }

    private static function formValues(array $payload) {
        $values = [];
        foreach (self::$FORM_FIELDS as $api => $field) {
            // exclude_none on the Python side: an absent key means "leave as is"
            if (array_key_exists($api, $payload) && $payload[$api] !== null && $payload[$api] !== '') {
                $values[$field] = $payload[$api];
            } elseif (array_key_exists($api, $payload) && $payload[$api] === '' && $api !== 'mobile') {
                $values[$field] = '';   // explicit clear; 'mobile' has no field to clear
            }
        }
        return $values;
    }

    private static function applyForm(User $user, array $payload) {
        $values = self::formValues($payload);
        if ($values && ($errors = FastApiModel::applyAnswers($user, $values, 'U'))) {
            throw new Exception('User data rejected: ' . json_encode($errors));
        }
    }

    public static function create($payload, $staff_id) {
        $email = self::requireEmail($payload['email'] ?? '');
        $name  = trim((string) ($payload['name'] ?? ''));
        if ($name === '') {
            throw new Exception("User 'name' is required.");
        }
        // User::fromVars() silently returns the existing user for a known email.
        if (User::lookupByEmail($email)) {
            throw new Exception("A user with the email {$email} already exists.");
        }

        $vars = ['name' => $name, 'email' => $email];
        if (!empty($payload['org_id'])) {
            $vars['org_id'] = self::requireOrg($payload['org_id'])->getId();
        }
        $user = User::fromVars($vars);
        if (!$user || !$user->getId()) {
            throw new Exception('User could not be created.');
        }
        try {
            self::applyForm($user, $payload);
        } catch (Exception $e) {
            $user->delete();   // don't leave a half-populated user behind
            throw $e;
        }
        return ['user_id' => $user->getId(), 'name' => $user->getName()->getFull(), 'email' => $user->getDefaultEmailAddress()];
    }

    public static function update($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);

        // Validate everything before changing anything.
        $email = isset($payload['email']) ? self::requireEmail($payload['email']) : null;
        if ($email !== null && ($other = User::lookupByEmail($email)) && $other->getId() != $user->getId()) {
            throw new Exception("The email {$email} belongs to another user.");
        }
        $name = null;
        if (isset($payload['name'])) {
            $name = trim((string) $payload['name']);
            if ($name === '') {
                throw new Exception("User 'name' cannot be empty.");
            }
        }
        $org = null;
        if (array_key_exists('org_id', $payload) && $payload['org_id'] !== null && (int) $payload['org_id'] !== 0) {
            $org = self::requireOrg($payload['org_id']);
        }

        self::applyForm($user, $payload);

        if ($name !== null) {
            $user->name = $name;
        }
        if ($email !== null && $user->default_email->address !== $email) {
            $user->default_email->address = $email;
            $user->default_email->save();
        }
        if (array_key_exists('org_id', $payload) && $payload['org_id'] !== null) {
            $user->set('org_id', $org ? $org->getId() : 0);   // 0 clears the membership
        }
        if (!$user->save()) {
            throw new Exception('User could not be saved.');
        }
        return ['user_id' => $user->getId()];
    }

    public static function delete($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        if (!$user->delete()) {
            throw new Exception('User could not be deleted (users that still have tickets cannot be deleted).');
        }
        return ['user_id' => (int) ($payload['user_id'] ?? 0)];
    }
}
