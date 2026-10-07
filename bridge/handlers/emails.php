<?php
/**
 * The system's email addresses (scp/emails.php). Mail accounts (mailbox fetching, SMTP) carry credentials and
 * connection tests and are not touched here: an address's accounts are left as they are. A new address goes through
 * Email::update() (it has no accounts); an update sets the basic fields directly and makes the same checks.
 */
include_once INCLUDE_DIR . 'class.email.php';

if (!class_exists('FastApiEmailHandler')) {

class FastApiEmailHandler {

    private static function requireEmail($email_id) {
        $email = Email::lookup((int) $email_id);
        if (!$email) {
            throw new Exception("Email not found: {$email_id}");
        }
        return $email;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function create($payload, $staff_id) {
        $email = Email::create();
        $errors = [];
        $vars = [
            'email'       => (string) ($payload['email'] ?? ''),
            'name'        => (string) ($payload['name'] ?? ''),
            'dept_id'     => (int) ($payload['dept_id'] ?? 0),
            'priority_id' => (int) ($payload['priority_id'] ?? 0),
            'topic_id'    => (int) ($payload['topic_id'] ?? 0),
            'noautoresp'  => (int) !empty($payload['no_autoresponse']),
            'notes'       => (string) ($payload['notes'] ?? ''),
        ];
        if (!$email->update($vars, $errors) || $errors || !$email->getId()) {
            throw new Exception('Email was not added: ' . self::errorText($errors ?: ['nothing was stored']));
        }
        return ['email_id' => $email->getId(), 'email' => $email->getEmail()];
    }

    /**
     * Updates the basic fields only. Email::update() would also hand its (empty) variables to the address's mailbox and
     * SMTP accounts and rewrite them, so the same checks are made here and the fields are set directly.
     */
    public static function update($payload, $staff_id) {
        global $cfg;
        $email = self::requireEmail($payload['email_id'] ?? 0);
        $address = trim(array_key_exists('email', $payload) ? (string) $payload['email'] : (string) $email->email);
        $name = trim(Format::striptags(array_key_exists('name', $payload) ? (string) $payload['name'] : (string) $email->name));
        $errors = [];
        if (!$address || !Validator::is_email($address)) {
            $errors['email'] = 'Valid email required';
        } elseif (($eid = Email::getIdByEmail($address)) && $eid != $email->getId()) {
            $errors['email'] = 'Email already exists';
        } elseif ($cfg && !strcasecmp($cfg->getAdminEmail(), $address)) {
            $errors['email'] = 'Email already used as admin email!';
        } elseif (Staff::getIdByEmail($address)) {
            $errors['email'] = 'Email in use by an agent';
        }
        if (!$name) {
            $errors['name'] = 'Email name required';
        }
        if ($errors) {
            throw new Exception('Email was not updated: ' . self::errorText($errors));
        }
        $email->email = Format::sanitize($address);
        $email->name = $name;
        if (array_key_exists('dept_id', $payload))         $email->dept_id = (int) $payload['dept_id'];
        if (array_key_exists('priority_id', $payload))     $email->priority_id = (int) $payload['priority_id'];
        if (array_key_exists('topic_id', $payload))        $email->topic_id = (int) $payload['topic_id'];
        if (array_key_exists('no_autoresponse', $payload)) $email->noautoresp = (int) !empty($payload['no_autoresponse']);
        if (array_key_exists('notes', $payload))           $email->notes = Format::sanitize((string) $payload['notes']);
        if (!$email->save()) {
            throw new Exception('Email could not be saved.');
        }
        return ['email_id' => $email->getId()];
    }

    public static function delete($payload, $staff_id) {
        global $cfg;
        $email = self::requireEmail($payload['email_id'] ?? 0);
        $id = $email->getId();
        if ($id == $cfg->getDefaultEmailId() || $id == $cfg->getAlertEmailId()) {
            throw new Exception('The default and the alert email address cannot be deleted.');
        }
        if (!$email->delete()) {
            throw new Exception('Email could not be deleted.');
        }
        return ['email_id' => $id];
    }
}
}
