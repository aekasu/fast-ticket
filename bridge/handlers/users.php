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
        FastApiModel::applyCustomFields($user, (array) ($payload['custom_fields'] ?? []), 'U');
    }

    /**
     * Import users from CSV: scp/users.php "import-users" -> User::importFromPost() — the same code behind osTicket's
     * "Import" dialog. All-or-nothing: osTicket imports inside a transaction and rolls back on the first bad row, and
     * the reason is its own message. 'pasted' = lines of "name, email" with no header row (the dialog's pasted box);
     * otherwise the CSV has a header row naming the user form's fields (name, email, phone, ...).
     * Payload: csv, pasted. Result: imported (how many rows).
     */
    public static function import($payload, $staff_id) {
        $csv = (string) ($payload['csv'] ?? '');
        if (trim($csv) === '') {
            throw new Exception("A 'csv' to import is required.");
        }
        try {
            $status = !empty($payload['pasted']) ? User::importFromPost($csv) : User::importCsv($csv);
        } finally {
            // User::importCsv() switches autocommit off and, when it refuses a row, rolls back without switching it
            // on again: left off, the plugin's "mark this command failed" update would never be committed.
            db_autocommit(true);
        }
        if (!is_numeric($status)) {
            throw new Exception('Import refused: ' . (is_string($status) ? $status : json_encode($status)));
        }
        return ['imported' => (int) $status];
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

    /** The end user's account row (login settings), or a refusal if the user never registered. */
    private static function requireAccount(User $user) {
        $account = $user->getAccount();
        if (!$account) {
            throw new Exception('This user has no account (register one first).');
        }
        return $account;
    }

    private static function accountEvent($subject, $key) {
        $type = ['type' => 'edited', 'key' => $key];   // Signal::send takes its data by reference
        Signal::send('object.edited', $subject, $type);
    }

    /**
     * Register an account: UserAccount::register(), as the "Register" pop-up. With send_email the user
     * gets an activation email and picks their own password; otherwise a temporary password is required
     * and the account is confirmed at once. Payload: user_id, username, timezone, password, send_email,
     * require_password_reset, forbid_password_reset.
     */
    public static function registerAccount($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        if ($user->getAccount()) {
            throw new Exception('User already registered.');
        }
        $send = !empty($payload['send_email']);
        $vars = ['username' => (string) ($payload['username'] ?? ''), 'timezone' => (string) ($payload['timezone'] ?? '')];
        if ($send) {
            $vars['sendemail'] = true;
        } else {
            $vars['passwd1'] = $vars['passwd2'] = (string) ($payload['password'] ?? '');
            if (!empty($payload['require_password_reset'])) $vars['pwreset-flag'] = 1;
            if (!empty($payload['forbid_password_reset'])) $vars['forbid-pwreset-flag'] = 1;
        }
        $errors = [];
        if (!UserAccount::register($user, $vars, $errors) || $errors) {
            throw new Exception('Account was not created: ' . json_encode($errors ?: 'registration failed'));
        }
        self::accountEvent($user, 'user-registered');
        return ['user_id' => $user->getId(), 'confirmed' => !$send];
    }

    /**
     * Change an account: UserAccount::update(). It overwrites username, timezone and every status flag
     * from its vars, so they start from the stored account and only the payload's keys are overlaid.
     * Payload: user_id, username, timezone, password (also confirms the account), is_locked,
     * require_password_reset, forbid_password_reset.
     */
    public static function updateAccount($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        $account = self::requireAccount($user);
        $vars = ['username' => (string) $account->username, 'timezone' => (string) $account->timezone];
        foreach (['username', 'timezone'] as $field) {
            if (array_key_exists($field, $payload)) {
                $vars[$field] = (string) $payload[$field];
            }
        }
        $flags = [
            'is_locked'              => ['locked-flag', UserAccountStatus::LOCKED],
            'require_password_reset' => ['pwreset-flag', UserAccountStatus::REQUIRE_PASSWD_RESET],
            'forbid_password_reset'  => ['forbid-pwchange-flag', UserAccountStatus::FORBID_PASSWD_RESET],
        ];
        foreach ($flags as $key => list($form_key, $bit)) {
            $on = array_key_exists($key, $payload) ? (bool) $payload[$key] : $account->getStatus()->check($bit);
            if ($on) $vars[$form_key] = 1;
        }
        if (!empty($payload['password'])) {
            $vars['passwd1'] = (string) $payload['password'];
        }
        $errors = [];
        if (!$account->update($vars, $errors) || $errors) {
            throw new Exception('Account was not updated: ' . json_encode($errors ?: 'update failed'));
        }
        return ['user_id' => $user->getId(), 'is_locked' => $account->isLocked(), 'is_confirmed' => $account->isConfirmed()];
    }

    /** Email the user a password-reset link (the Reset Password action). */
    public static function sendPasswordReset($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        $account = self::requireAccount($user);
        if (!$account->sendResetEmail()) {
            throw new Exception('The password reset email could not be sent.');
        }
        self::accountEvent($account, 'pwreset-sent');
        return ['user_id' => $user->getId()];
    }

    /** Email the user an account-activation link (the Send Activation Email action). */
    public static function sendConfirmation($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        $account = self::requireAccount($user);
        if ($account->isConfirmed()) {
            throw new Exception('Account is already confirmed.');
        }
        if (!$account->sendConfirmEmail()) {
            throw new Exception('The activation email could not be sent.');
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
