<?php
/**
 * Helpers shared by the command handlers.
 */

if (!class_exists('FastApiModel')) {
class FastApiModel {

    /**
     * Save a model through its osTicket `update($vars, &$errors)` method.
     *
     * osTicket's ORM only marks a field dirty when its value changes, and a field that
     * was never set compares equal to 0/null. So on a brand-new row an intended
     * "false"/0 (a private department, a disabled team or SLA, ...) is never written and
     * the column default (usually 1) wins. The row exists after the first pass, so a
     * second update() with the row's id now sees a real change and persists it.
     *
     * Existing rows need only the single pass.
     */
    static function saveViaUpdate($model, array $vars, $label) {
        $is_new = !$model->getId();
        $errors = [];
        if (!$model->update($vars, $errors) || $errors) {
            throw new Exception("{$label} not saved: " . json_encode($errors ?: 'save failed'));
        }
        if (!$model->getId()) {
            throw new Exception("{$label} was not persisted.");
        }
        if ($is_new) {
            $vars['id'] = $model->getId();
            $errors = [];
            if (!$model->update($vars, $errors) || $errors) {
                throw new Exception("{$label} created but its settings could not be applied: " . json_encode($errors ?: 'save failed'));
            }
        }
    }

    /**
     * Write name => value pairs into the dynamic-form entries of an owner (Organization,
     * User, ...) and save them. Returns [] on success or [field => [messages]] if any
     * value is rejected, in which case nothing is saved.
     *
     * osTicket's own update() methods validate against the global $_POST, which does
     * not exist under cron, and an existing answer ignores a data source. The route that
     * does work outside a web request is the one used here: parse() the raw value the way
     * getClean() would (a phone loses its punctuation), validateEntry() the parsed value,
     * then DynamicFormEntry::setAnswer() + save().
     *
     * $form_type is DynamicForm.type ('O' organization, 'U' user).
     */
    /**
     * Validate $value for the entry's field $name and store it as the entry's answer. Returns the
     * field's error list ([] on success). osTicket keeps answers in their database form: a choice is
     * stored as its JSON label map plus the key in value_id, so the cleaned value goes through
     * to_database() first. Presentation-only and non-editable fields are refused.
     */
    static function setEntryAnswer($entry, $name, $value, $staff_view = true) {
        if (!($field = $entry->getField($name))) {
            return ["The form has no '{$name}' field"];
        }
        if (!$field->hasData() || $field->isPresentationOnly()) {
            return ["'{$name}' is not a field that holds an answer"];
        }
        if ($staff_view && !($field->isVisibleToStaff() && $field->isEditableToStaff())) {
            return ["'{$name}' cannot be edited by staff"];
        }
        $clean = $field->parse($value);
        // A choice field's parse() turns an unknown key into an empty value without complaint; a value
        // that was given but parsed to nothing is a rejection, not a clear.
        $given = !($value === '' || $value === [] || $value === null || $value === false);
        if ($given && ($clean === null || $clean === [] || $clean === '')) {
            return [sprintf("%s is not a valid value for '%s'", is_scalar($value) ? "'{$value}'" : json_encode($value), $name)];
        }
        $field->validateEntry($clean);
        if ($field->errors()) {
            return array_values((array) $field->errors());
        }
        // ChoiceField::validateEntry() does not check membership: every given key must be a choice.
        if ($given && $field instanceof ChoiceField) {
            $choices = $field->getChoices();
            foreach ((array) $value as $key) {
                if (!is_scalar($key) || !isset($choices[$key])) {
                    return ["'" . (is_scalar($key) ? $key : json_encode($key)) . "' is not one of the choices of '{$name}' ("
                        . implode(', ', array_keys($choices)) . ')'];
                }
            }
        }
        $db = $field->to_database($clean);
        if (is_array($db)) {
            $entry->setAnswer($name, $db[0], $db[1]);
        } else {
            $entry->setAnswer($name, $db);
        }
        return [];
    }

    /**
     * Set custom-field answers ({variable name => value}) on the owner's form entries of $form_type
     * (a user, organization or task has one entry of its type). Every name must be a field of that
     * form that staff may edit; nothing is saved unless all values are accepted.
     */
    static function applyCustomFields($owner, array $custom, $form_type) {
        if (!$custom) {
            return;
        }
        $errors = [];
        $entries = [];
        foreach ($owner->getDynamicData() as $entry) {
            $form = $entry->getDynamicForm();
            if (!$form || $form->get('type') !== $form_type) {
                continue;
            }
            $entry->addMissingFields();
            $entries[] = $entry;
        }
        foreach ($custom as $name => $value) {
            $errs = ["The form has no '{$name}' field"];
            foreach ($entries as $entry) {
                if ($entry->getField($name)) {
                    $errs = self::setEntryAnswer($entry, $name, $value);
                    break;
                }
            }
            if ($errs) {
                $errors[$name] = $errs;
            }
        }
        if ($errors) {
            throw new Exception('Custom fields rejected: ' . json_encode($errors));
        }
        foreach ($entries as $entry) {
            $entry->save();
        }
    }

    /**
     * Keys that Ticket::create() / Task::create() read from their $vars to decide *what happens* (who the
     * object is assigned to, which SLA, whom to copy, what it links to). A custom field must never reach them.
     */
    const CREATE_CONTROL_KEYS = [
        'assignId', 'staffId', 'teamId', 'slaId', 'ccs', 'emailId', 'statusId', 'autorespond', 'alert',
        'deptId', 'topicId', 'priorityId', 'source', 'ip', 'ip_address', 'uid', 'duedate', 'message',
        'object_id', 'object_type', 'poster', 'default_formdata', 'internal_formdata', 'description',
        'user_id', 'userId', 'flags', 'number', 'created', 'lastupdate', 'staff_id', 'team_id', 'dept_id',
        // read by Ticket::open() (the staff path): the first response, the note, who is emailed
        'response', 'note', 'reply-to', 'signature', 'cannedResponseId', 'mailflags', 'system_emails', 'title',
        'ticket', 'msgId', 'files', 'cannedattachments',
    ];

    /**
     * Check the {variable name => value} map of a create request against the forms that really define the
     * object's custom fields, and return the entries to merge into the create vars. Refuses unknown names,
     * the built-in fields ($builtin) and the control keys above, whatever a form calls its field.
     */
    static function checkCreateCustomFields(array $custom, array $forms, array $builtin) {
        $allowed = [];
        foreach ($forms as $form) {
            foreach ($form->getFields() as $field) {
                if ($field->hasData() && !$field->isPresentationOnly()
                        && $field->isVisibleToStaff() && $field->isEditableToStaff()) {
                    $allowed[$field->get('name')] = true;
                }
            }
        }
        $errors = [];
        foreach ($custom as $name => $value) {
            $name = (string) $name;
            if (in_array($name, $builtin, true) || in_array(strtolower($name), array_map('strtolower', self::CREATE_CONTROL_KEYS), true)) {
                $errors[$name] = ["'{$name}' is a built-in field, not a custom one"];
            } elseif (!isset($allowed[$name])) {
                $errors[$name] = ["The form has no '{$name}' field"];
            }
        }
        if ($errors) {
            throw new Exception('Custom fields rejected: ' . json_encode($errors));
        }
        return $custom;
    }

    static function applyAnswers($owner, array $values, $form_type) {
        $errors = [];
        $entries = [];
        foreach ($owner->getDynamicData() as $entry) {
            $form = $entry->getDynamicForm();
            if (!$form || $form->get('type') !== $form_type)
                continue;
            $entry->addMissingFields();
            foreach ($values as $name => $value) {
                if (!($field = $entry->getField($name))) {
                    $errors[$name] = ["The form has no '{$name}' field"];
                    continue;
                }
                $clean = $field->parse($value);
                $field->validateEntry($clean);
                if ($field->errors()) {
                    $errors[$name] = $field->errors();
                    continue;
                }
                $entry->setAnswer($name, $clean);
            }
            $entries[] = $entry;
        }
        if (!$errors) {
            foreach ($entries as $entry)
                $entry->save();
        }
        return $errors;
    }
}
}

require_once INCLUDE_DIR . 'class.thread_actions.php';

if (!class_exists('FastApiThreadEntry')) {
/**
 * Thread-entry edit and resend, shared by tickets and tasks: osTicket's TEA_* thread actions, which
 * work on whatever object owns the thread. The caller has already checked that the acting staff
 * member may open the object.
 */
class FastApiThreadEntry {

    /** The entry, checked to belong to $object's thread ($type: 'T' ticket, 'A' task). */
    static function entryOf($object, $type, $entry_id) {
        $entry = ThreadEntry::lookup((int) $entry_id);
        if (!$entry || !$entry->getThread() || $entry->getThread()->getObjectId() != $object->getId()
                || $entry->getThread()->getObjectType() !== $type) {
            throw new Exception("Thread entry not found on this " . ($type === 'T' ? 'ticket' : 'task') . ": {$entry_id}");
        }
        return $entry;
    }

    /**
     * TEA_EditThreadEntry::updateEntry(): writes a new entry (child of the old) and hides the old
     * one, so the entry id changes. Who may edit is the action's own isEnabled(). With resend
     * (replies only) the edited reply is also sent again. Payload: body, title, resend, signature.
     */
    static function edit($entry, array $payload) {
        $resend = !empty($payload['resend']);
        $action = $resend ? new TEA_EditAndResendThreadEntry($entry) : new TEA_EditThreadEntry($entry);
        // osTicket: system posts cannot be edited; only staff replies can be resent
        if (!($entry->staff_id || $entry->user_id)) {
            throw new Exception('System entries cannot be edited.');
        }
        if ($resend && !($entry->staff_id && $entry->type == 'R')) {
            throw new Exception('Only a staff reply can be resent.');
        }
        if (!$action->isEnabled()) {
            throw new Exception('Permission denied: the requesting staff member may not edit this entry.');
        }
        $body = (string) ($payload['body'] ?? '');
        if (trim($body) === '') {
            throw new Exception("An entry 'body' is required.");
        }
        $_POST = ['body' => $body, 'title' => (string) ($payload['title'] ?? $entry->title),
                  'signature' => (string) ($payload['signature'] ?? 'none')];
        $_SERVER['REMOTE_ADDR'] = $_SERVER['REMOTE_ADDR'] ?? '127.0.0.1';
        $new = $action->updateEntry($resend);
        if (!$new) {
            throw new Exception('Entry was not edited.');
        }
        $changed = $new->getId() != $entry->getId();
        if ($resend) {
            $action->resend($new);
        }
        return ['entry_id' => (int) $entry->getId(), 'new_entry_id' => (int) $new->getId(),
                'changed' => $changed, 'resent' => $resend];
    }

    /** TEA_ResendThreadEntry (enabled for any agent who can open the object). */
    static function resend($entry, array $payload) {
        if (!($entry->staff_id && $entry->type == 'R')) {
            throw new Exception('Only a staff reply can be resent.');
        }
        $_POST = ['signature' => (string) ($payload['signature'] ?? 'none')];
        (new TEA_ResendThreadEntry($entry))->resend($entry);
        return ['entry_id' => (int) $entry->getId()];
    }
}
}
