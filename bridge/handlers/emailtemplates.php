<?php
/**
 * Email template sets and their messages (scp/templates.php) through EmailTemplateGroup / EmailTemplate, which write
 * with SQL of their own and validate the name and the message text. A set's update rewrites name, status and notes, so
 * it starts from the stored values. A set in use (the default one, or a department's) cannot be disabled or deleted.
 */
include_once INCLUDE_DIR . 'class.template.php';

if (!class_exists('FastApiEmailTemplateHandler')) {

class FastApiEmailTemplateHandler {

    private static function requireSet($set_id) {
        $set = EmailTemplateGroup::lookup((int) $set_id);
        if (!$set) {
            throw new Exception("Template set not found: {$set_id}");
        }
        return $set;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function create($payload, $staff_id) {
        $errors = [];
        $vars = [
            'name'     => (string) ($payload['name'] ?? ''),
            'notes'    => (string) ($payload['notes'] ?? ''),
            'isactive' => (int) (array_key_exists('is_active', $payload) ? !empty($payload['is_active']) : true),
            'tpl_id'   => (int) ($payload['copy_from_id'] ?? 0),
            'lang_id'  => (string) ($payload['lang'] ?? ''),
        ];
        $id = EmailTemplateGroup::create($vars, $errors);
        if (!$id || $errors) {
            throw new Exception('Template set was not created: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['set_id' => (int) $id];
    }

    public static function update($payload, $staff_id) {
        $set = self::requireSet($payload['set_id'] ?? 0);
        $vars = [
            'tpl_id'   => $set->getId(),
            'name'     => array_key_exists('name', $payload) ? (string) $payload['name'] : $set->getName(),
            'notes'    => array_key_exists('notes', $payload) ? (string) $payload['notes'] : (string) $set->getNotes(),
            'isactive' => (int) (array_key_exists('is_active', $payload) ? !empty($payload['is_active']) : $set->isActive()),
            'lang_id'  => array_key_exists('lang', $payload) ? (string) $payload['lang'] : '',
        ];
        $errors = [];
        if (!$set->update($vars, $errors) || $errors) {
            throw new Exception('Template set was not updated: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['set_id' => $set->getId()];
    }

    public static function delete($payload, $staff_id) {
        $set = self::requireSet($payload['set_id'] ?? 0);
        $id = $set->getId();
        if ($set->isInUse()) {
            throw new Exception('A template set in use cannot be deleted.');
        }
        if (!$set->delete()) {
            throw new Exception('Template set could not be deleted.');
        }
        return ['set_id' => $id];
    }

    /** Set a message's subject and body; a message the set has no copy of yet is added (the page's "implement"). */
    public static function updateMessage($payload, $staff_id) {
        $set = self::requireSet($payload['set_id'] ?? 0);
        $code = (string) ($payload['code_name'] ?? '');
        if (!array_key_exists($code, EmailTemplateGroup::$all_names)) {
            throw new Exception("Unknown message template: {$code}");
        }
        $vars = ['subject' => (string) ($payload['subject'] ?? ''), 'body' => (string) ($payload['body'] ?? '')];
        $errors = [];
        $template = EmailTemplate::lookupByName($set->getId(), $code, $set);
        if ($template) {
            if (!$template->update($vars, $errors) || $errors) {
                throw new Exception('Message was not updated: ' . self::errorText($errors ?: ['unknown error']));
            }
            return ['set_id' => $set->getId(), 'code_name' => $code, 'created' => false];
        }
        $vars['tpl_id'] = $set->getId();
        $vars['code_name'] = $code;
        $new = EmailTemplate::add($vars, $errors);
        if (!$new || $errors) {
            throw new Exception('Message was not stored: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['set_id' => $set->getId(), 'code_name' => $code, 'created' => true];
    }
}
}
