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

    // ── fields ────────────────────────────────────────────────────────────────

    /** Requirement modes: API name => DynamicFormField::allRequirementModes() key. */
    private static $MODES = [
        'optional' => 'a', 'required' => 'b', 'required_for_users' => 'c', 'required_for_agents' => 'd',
        'internal_optional' => 'e', 'internal_required' => 'f', 'users_only' => 'g',
    ];

    private static function requireField(DynamicForm $form, $field_id) {
        $field = DynamicFormField::lookup((int) $field_id);
        if (!$field || (int) $field->form_id !== (int) $form->getId()) {
            throw new Exception("Field {$field_id} is not a field of this form.");
        }
        return $field;
    }

    private static function validType($type) {
        if (!FormField::getFieldType($type)) {
            throw new Exception("Unknown field type: {$type}");
        }
        return $type;
    }

    /**
     * Merge a configuration payload over the stored one. Only options the field's type defines are
     * accepted; values are cast the way osTicket stores them (booleans, integers, strings) and the
     * type's own to_config() gets the last word, as DynamicFormField::setConfiguration() does.
     */
    private static function mergedConfiguration($type, $stored_json, array $changes) {
        $impl = (new FormField(['type' => $type, 'label' => 'x', 'name' => 'x', 'id' => 0]))->getImpl();
        $options = $impl->getConfigurationOptions();
        $config = JsonDataParser::decode((string) $stored_json) ?: [];
        foreach ($changes as $name => $value) {
            if (!isset($options[$name])) {
                throw new Exception("Option '{$name}' does not apply to a '{$type}' field (allowed: "
                    . implode(', ', array_keys($options)) . ').');
            }
            $kind = get_class($options[$name]);
            if ($kind === 'BooleanField')      $value = (bool) $value;
            elseif (is_int($value))            $value = $value;
            else                               $value = (string) $value;
            $config[$name] = $value;
        }
        if (($config['validator'] ?? '') === 'regex' && trim((string) ($config['regex'] ?? '')) === '') {
            throw new Exception("A 'regex' validator needs a regex.");
        }
        return JsonDataEncoder::encode($impl->to_config($config));
    }

    private static function checkFieldNames(DynamicForm $form, DynamicFormField $field) {
        $name = strtolower((string) $field->get('name'));
        if ($name === '') {
            return;
        }
        foreach ($form->getDynamicFields() as $other) {
            if ((int) $other->get('id') !== (int) $field->get('id') && strtolower((string) $other->get('name')) === $name) {
                throw new Exception('Field variable name is not unique: ' . $field->get('name'));
            }
        }
    }

    private static function validateField(DynamicForm $form, DynamicFormField $field) {
        if (!$field->isValid()) {
            throw new Exception('Field rejected: ' . json_encode($field->errors()));
        }
        // the ticket form's Issue Summary must always take user input
        if ($form->get('type') == 'T' && $field->get('name') == 'subject'
                && ($f = $field->getField()->getImpl()) && !$f->hasData()) {
            throw new Exception('The issue summary must be a field that supports user input, such as short answer.');
        }
        self::checkFieldNames($form, $field);
    }

    /** Add a field: what scp/forms.php does for a "new" row of the form's field table. */
    public static function addField($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        $type = self::validType((string) ($payload['type'] ?? ''));
        $max = 0;
        foreach ($form->getDynamicFields() as $f) {
            $max = max($max, (int) $f->get('sort'));
        }
        $field = DynamicFormField::create([
            'sort'  => isset($payload['sort']) ? (int) $payload['sort'] : $max + 1,
            'label' => trim((string) ($payload['label'] ?? '')),
            'type'  => $type,
            'name'  => trim((string) ($payload['name'] ?? '')),
        ]);
        if ($field->setRequirementMode(self::$MODES[$payload['mode'] ?? 'optional'] ?? 'a') === false) {
            throw new Exception('That requirement mode is not available for this field.');
        }
        if (isset($payload['hint'])) {
            $field->set('hint', Format::sanitize($payload['hint']) ?: null);
        }
        if (!empty($payload['configuration'])) {
            $field->set('configuration', self::mergedConfiguration($type, null, $payload['configuration']));
        }
        $form->fields->add($field);
        self::validateField($form, $field);
        $field->form = $form;
        if (!$field->save(true) || !$field->get('id')) {
            throw new Exception('Field could not be saved.');
        }
        return ['form_id' => $form->getId(), 'field_id' => (int) $field->get('id')];
    }

    /** Change a field: scp/forms.php's row edit plus the field-configuration pop-up (ajax.forms.php). */
    public static function updateField($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        $field = self::requireField($form, $payload['field_id'] ?? 0);

        if (isset($payload['type']) && $payload['type'] !== $field->get('type')) {
            if (!$field->isChangeable()) {
                throw new Exception("The type of the '{$field->get('label')}' field cannot be changed.");
            }
            $field->set('type', self::validType($payload['type']));
            $field->set('configuration', null);          // the old type's options do not apply
        }
        if (isset($payload['name']) && $payload['name'] !== $field->get('name')) {
            if ($field->isNameForced()) {
                throw new Exception("The variable name of the '{$field->get('label')}' field cannot be changed.");
            }
            $field->set('name', trim($payload['name']));
        }
        if (isset($payload['label']))  $field->set('label', trim($payload['label']));
        if (isset($payload['sort']))   $field->set('sort', (int) $payload['sort']);
        if (array_key_exists('hint', $payload)) $field->set('hint', Format::sanitize($payload['hint']) ?: null);

        if (isset($payload['mode'])) {
            $letter = self::$MODES[$payload['mode']] ?? null;
            if ($letter === null || $field->setRequirementMode($letter) === false) {
                throw new Exception("The '{$payload['mode']}' mode is not available for the '{$field->get('label')}' field.");
            }
        }
        if (isset($payload['is_enabled'])) {
            if (!$payload['is_enabled'] && $field->hasFlag(DynamicFormField::FLAG_MASK_DISABLE)) {
                throw new Exception("The '{$field->get('label')}' field cannot be disabled.");
            }
            $flags = (int) $field->get('flags');
            $field->set('flags', $payload['is_enabled'] ? ($flags | DynamicFormField::FLAG_ENABLED) : ($flags & ~DynamicFormField::FLAG_ENABLED));
        }
        if (!empty($payload['configuration'])) {
            $field->set('configuration', self::mergedConfiguration($field->get('type'), $field->get('configuration'), $payload['configuration']));
        }
        // a type change drops the configuration; a type that needs none is fine with null
        self::validateField($form, $field);
        if (!$field->save(true)) {
            throw new Exception('Field could not be saved.');
        }
        return ['form_id' => $form->getId(), 'field_id' => (int) $field->get('id')];
    }

    /** Remove a field; one with answers is only detached from the form unless delete_data is set. */
    public static function deleteField($payload, $requester_staff_id) {
        $form = self::requireForm($payload['form_id'] ?? 0);
        $field = self::requireField($form, $payload['field_id'] ?? 0);
        if (!$field->isDeletable()) {
            throw new Exception("The '{$field->get('label')}' field is part of osTicket's core form and cannot be deleted.");
        }
        if (!empty($payload['delete_data'])) {
            DynamicFormEntryAnswer::objects()->filter(['field_id' => (int) $field->get('id')])->delete();
        }
        $id = (int) $field->get('id');
        $field->delete();
        return ['form_id' => $form->getId(), 'field_id' => $id];
    }
}
