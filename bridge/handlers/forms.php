<?php
/**
 * Dynamic Form command handlers.
 *
 * Allows create/update/delete of osTicket DynamicForms.
 * Payload field names match app/modules/forms/models.py (FormCreate,
 * FormUpdate).
 *
 * NOTE: DynamicForm creation/deletion is restricted to admin in osTicket.
 * That permission is enforced at the FastAPI layer; the bridge trusts it.
 */

require_once INCLUDE_DIR . 'class.dynamic.forms.php';

class FastApiFormHandler {

    private static function requireForm($form_id) {
        $form = DynamicForm::lookup((int) $form_id);
        if (!$form) {
            throw new Exception("Dynamic form not found: {$form_id}");
        }
        return $form;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'title'  => $payload['title'] ?? '',
            'type'   => $payload['type'] ?? 'G', // G=generic, T=ticket, U=user, O=org
            'notes'  => $payload['notes'] ?? '',
        ];
        $errors = [];
        $form = DynamicForm::create($vars, $errors);
        if (!$form) {
            throw new Exception('DynamicForm::create failed: ' . implode(', ', $errors));
        }
        return ['form_id' => $form->getId(), 'title' => $form->getTitle()];
    }

    public static function update($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        $vars = [];
        foreach (['title', 'type', 'notes'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        $errors = [];
        if (!$form->update($vars, $errors)) {
            throw new Exception('DynamicForm::update failed: ' . implode(', ', $errors));
        }
        return ['form_id' => $form->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        // DynamicForm::delete() returns false for system forms (is_deletable=0).
        if (!$form->delete()) {
            throw new Exception('DynamicForm::delete() failed — form may be a protected system form.');
        }
        return ['form_id' => (int) ($payload['form_id'] ?? 0)];
    }
}
