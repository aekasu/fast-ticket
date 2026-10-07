<?php
/**
 * The requesting agent's own profile (scp/profile.php) and password (the "Change Password" dialog,
 * ajax.staff.php changePassword). Both act on $thisstaff only — no staff id is read from the payload.
 * Staff::updateProfile() overwrites every field it knows, so an update starts from the stored values and
 * overlays what the payload sends.
 */
if (!class_exists('FastApiProfileHandler')) {

class FastApiProfileHandler {

    private const SETTINGS = ['datetime_format', 'default_from_name', 'thread_view_order', 'default_ticket_queue_id',
        'reply_redirect', 'img_att_view', 'editor_spacing'];

    private static function me() {
        global $thisstaff;
        $staff = $thisstaff ? Staff::lookup($thisstaff->getId()) : null;
        if (!$staff) {
            throw new Exception('Requesting staff member not found.');
        }
        return $staff;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function update($payload, $staff_id) {
        $staff = self::me();
        $stored = (new Config('staff.' . $staff->getId()))->getInfo();
        $vars = [
            'id'                     => $staff->getId(),
            'firstname'              => $staff->firstname,
            'lastname'               => $staff->lastname,
            'email'                  => $staff->email,
            'phone'                  => $staff->phone,
            'phone_ext'              => $staff->phone_ext,
            'mobile'                 => $staff->mobile,
            'signature'              => $staff->signature,
            'timezone'               => $staff->timezone,
            'locale'                 => $staff->locale,
            'lang'                   => $staff->lang,
            'max_page_size'          => $staff->max_page_size,
            'auto_refresh_rate'      => $staff->auto_refresh_rate,
            'default_signature_type' => $staff->default_signature_type,
            'default_paper_size'     => $staff->default_paper_size,
            'default_2fa'            => $stored['default_2fa'] ?? null,
        ];
        if ($staff->onvacation) {
            $vars['onvacation'] = 1;   // updateProfile() reads the key's presence, not its value
        }
        foreach (self::SETTINGS as $key) {
            $vars[$key] = $stored[$key] ?? null;
        }
        foreach ($payload as $key => $value) {
            if ($key === 'onvacation') {
                if ($value) {
                    $vars['onvacation'] = 1;
                } else {
                    unset($vars['onvacation']);
                }
            } elseif (array_key_exists($key, $vars) && $key !== 'id' && $key !== 'default_2fa') {
                $vars[$key] = $value;
            }
        }
        // updateProfile() reads the stored avatar key only when given, so it is left alone
        $errors = [];
        if (!$staff->updateProfile($vars, $errors) || $errors) {
            throw new Exception('Profile was not updated: ' . self::errorText($errors));
        }
        return ['staff_id' => $staff->getId()];
    }

    public static function changePassword($payload, $staff_id) {
        $staff = self::me();
        $current = (string) ($payload['current_password'] ?? '');
        $new = (string) ($payload['new_password'] ?? '');
        if ($new === '') {
            throw new Exception('A new password is required.');
        }
        // osTicket's change dialog asks for the current password; a local account is checked here, another
        // backend (LDAP...) checks it itself in setPassword()
        $backend = $staff->getAuthBackend();
        if ((!$backend || $backend instanceof osTicketStaffAuthentication) && !$staff->check_passwd($current, false)) {
            throw new Exception('Current password is incorrect.');
        }
        try {
            Staff::checkPassword($new, null);   // the policy as the dialog's validators run it
            $staff->setPassword($new, $current);
        } catch (BadPassword $e) {
            throw new Exception('New password refused: ' . $e->getMessage());
        } catch (PasswordUpdateFailed $e) {
            throw new Exception('Password update failed: ' . $e->getMessage());
        }
        if (!$staff->save()) {
            throw new Exception('Password could not be saved.');
        }
        return ['staff_id' => $staff->getId()];
    }
}
}
