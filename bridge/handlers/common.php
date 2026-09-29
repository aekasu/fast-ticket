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
