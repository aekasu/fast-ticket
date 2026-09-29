<?php
/**
 * Dynamic Form command handlers.
 *
 * Allows create/update/delete of osTicket DynamicForms, mirroring scp/forms.php.
 * Payload field names match app/modules/forms/models.py (FormCreate, FormUpdate).
 *
 * NOTE: DynamicForm creation/deletion is restricted to admin in osTicket.
 * That permission is enforced at the FastAPI layer; the bridge trusts it.
 *
 * DynamicForm::delete() is a *soft* delete — it sets FLAG_DELETED and keeps the
 * row — and refuses forms without FLAG_DELETABLE. This handler additionally refuses every
 * form that is not a custom one (type G).
 */

require_once INCLUDE_DIR . 'class.dynamic_forms.php';

class FastApiFormHandler {

    private static function requireForm($form_id) {
        $form = DynamicForm::lookup((int) $form_id);
        if (!$form || $form->hasFlag(DynamicForm::FLAG_DELETED)) {
            throw new Exception("Dynamic form not found: {$form_id}");
        }
        return $form;
    }

    public static function create($payload, $requester_staff_id) {
        $title = trim($payload['title'] ?? '');
        if ($title === '') {
            throw new Exception('A form title is required.');
        }

        // DynamicForm::create() only instantiates; save(true) inserts and re-reads the row.
        $form = DynamicForm::create();
        $form->set('title', $title);
        $form->set('instructions', $payload['instructions'] ?? '');
        $form->set('notes', $payload['notes'] ?? '');
        $form->set('type', $payload['type'] ?? 'G'); // G=generic, T=ticket, U=user, O=org
        if (!$form->save(true) || !$form->getId()) {
            throw new Exception('DynamicForm could not be saved.');
        }
        return ['form_id' => $form->getId(), 'title' => $form->getTitle()];
    }

    public static function update($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        foreach (['title', 'instructions', 'notes'] as $field) {
            if (isset($payload[$field])) {
                $form->set($field, $payload[$field]);
            }
        }
        if (trim((string) $form->get('title')) === '') {
            throw new Exception('A form title is required.');
        }
        if (!$form->save(true)) {
            throw new Exception('DynamicForm could not be updated.');
        }
        return ['form_id' => $form->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        // osTicket flags even its own core forms (Contact Information, Ticket Details, ...)
        // deletable, and deleting one breaks ticket / user / organization handling. Only
        // custom forms (type G) may be removed through the API.
        if ($form->get('type') !== 'G') {
            throw new Exception("'" . $form->getTitle() . "' is a core osTicket form (type " . $form->get('type') . "); only custom forms can be deleted.");
        }
        if (!$form->isDeletable()) {
            throw new Exception('This form is protected and cannot be deleted.');
        }
        if (!$form->delete()) {
            throw new Exception('DynamicForm::delete() failed.');
        }
        return ['form_id' => (int) ($payload['form_id'] ?? 0)];
    }
}
