<?php
/**
 * Lookup command handlers: Help Topics, SLAs, Priorities.
 *
 * Three classes in one file to keep related lookups together and avoid
 * a handler-file-per-class explosion for small domains.
 *
 * Payload field names match app/modules/lookups/models.py (TopicCreate,
 * SLACreate, PriorityCreate, and their *Update counterparts).
 *
 * Topic::create() / SLA::create() only instantiate; ->update($vars, &$errors)
 * validates and saves, and it OVERWRITES every field from $vars. So updates start
 * from the stored state (storedVars) and overlay only what the payload provides.
 */

require_once INCLUDE_DIR . 'class.topic.php';
require_once INCLUDE_DIR . 'class.dynamic_forms.php';
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.team.php';
require_once INCLUDE_DIR . 'class.sla.php';
require_once INCLUDE_DIR . 'class.schedule.php';
require_once INCLUDE_DIR . 'class.priority.php';
require_once dirname(__FILE__) . '/common.php';


// ── Help Topics ───────────────────────────────────────────────────────────────

/**
 * Mirrors osTicket's help-topic page. State is `status` (active|disabled|archived): Topic::update()
 * also assigns $this->isactive, but ost_help_topic has no such column in 1.18, so it is never passed.
 * Payload: topic, status, is_public, parent_id, dept_id, priority_id, status_id, sla_id, page_id, notes,
 * no_auto_response, custom_numbers + sequence_id + number_format, assign_staff_id / assign_team_id
 * (auto-assignment, 0 = none) and form_ids (ordered; replaces the attached forms — the core ticket form
 * is never detached, and fields a form had switched off for this topic stay off).
 *
 * Topic::updateForms() only touches forms when `forms` is an array, and then rewrites every form's
 * "disabled fields" from `fields` (the ids that stay enabled). So updates always pass both, rebuilt
 * from the stored state.
 */
class FastApiTopicHandler {

    private static function requireTopic($topic_id) {
        $topic = Topic::lookup((int) $topic_id);
        if (!$topic) {
            throw new Exception("Help topic not found: {$topic_id}");
        }
        return $topic;
    }

    /** [form_id => [field ids switched off for this topic]] in attachment order. */
    private static function storedForms(Topic $topic) {
        $forms = [];
        foreach (TopicFormModel::objects()->filter(['topic_id' => $topic->getId()])->order_by('sort') as $F) {
            $extra = JsonDataParser::decode($F->extra) ?: [];
            $forms[(int) $F->form_id] = array_map('intval', $extra['disable'] ?? []);
        }
        return $forms;
    }

    private static function fieldIds($form_id) {
        $ids = [];
        if ($form = DynamicForm::lookup((int) $form_id)) {
            foreach ($form->fields->values_flat('id') as $row) {
                $ids[] = (int) $row[0];
            }
        }
        return $ids;
    }

    /** `forms` + `fields` vars for a given ordered form list, keeping what is already switched off. */
    private static function formVars(array $form_ids, array $disabled_by_form) {
        $enabled = [];
        foreach ($form_ids as $form_id) {
            $enabled = array_merge($enabled, array_diff(self::fieldIds($form_id), $disabled_by_form[$form_id] ?? []));
        }
        return ['forms' => array_values($form_ids), 'fields' => array_values($enabled)];
    }

    private static function storedVars(Topic $topic) {
        $flags = (int) $topic->flags;
        $vars = [
            'id'          => $topic->getId(),
            'topic'       => $topic->topic,
            'topic_pid'   => $topic->topic_pid,
            'dept_id'     => $topic->dept_id,
            'priority_id' => $topic->priority_id,
            'status_id'   => $topic->status_id,
            'sla_id'      => $topic->sla_id,
            'page_id'     => $topic->page_id,
            'ispublic'    => (int) $topic->ispublic,
            'sequence_id' => $topic->sequence_id,
            'number_format' => $topic->number_format,
            'notes'       => $topic->notes,
            'status'      => ($flags & Topic::FLAG_ARCHIVED) ? 'archived'
                               : (($flags & Topic::FLAG_ACTIVE) ? 'active' : 'disabled'),
        ];
        if ($flags & Topic::FLAG_CUSTOM_NUMBERS) $vars['custom-numbers'] = 1;
        if ($topic->noautoresp)                  $vars['noautoresp'] = 1; // isset(): present = on
        // Auto-assignment is overloaded into one field: 's<staff_id>' or 't<team_id>'.
        if ($topic->staff_id)     $vars['assign'] = 's' . $topic->staff_id;
        elseif ($topic->team_id)  $vars['assign'] = 't' . $topic->team_id;
        $stored = self::storedForms($topic);
        return $vars + self::formVars(array_keys($stored), $stored);
    }

    private static function overlay(array $vars, array $payload, array $stored_forms = []) {
        foreach (['topic', 'dept_id', 'priority_id', 'status_id', 'sla_id', 'page_id', 'notes', 'sequence_id', 'number_format'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (isset($payload['parent_id'])) {
            $vars['topic_pid'] = (int) $payload['parent_id'];
        }
        if (isset($payload['is_public'])) {
            $vars['ispublic'] = (int) $payload['is_public'];
        }
        if (isset($payload['status'])) {
            $vars['status'] = $payload['status'];
        }
        foreach (['no_auto_response' => 'noautoresp', 'custom_numbers' => 'custom-numbers'] as $key => $form_key) {
            if (isset($payload[$key])) {
                if ($payload[$key]) $vars[$form_key] = 1; else unset($vars[$form_key]);
            }
        }
        if (isset($payload['assign_staff_id']) || isset($payload['assign_team_id'])) {
            $staff = (int) ($payload['assign_staff_id'] ?? 0);
            $team = (int) ($payload['assign_team_id'] ?? 0);
            if ($staff && !Staff::lookup($staff)) {
                throw new Exception("Staff not found: {$staff}");
            }
            if ($team && !Team::lookup($team)) {
                throw new Exception("Team not found: {$team}");
            }
            unset($vars['assign']);
            if ($staff)     $vars['assign'] = 's' . $staff;
            elseif ($team)  $vars['assign'] = 't' . $team;
        }
        if (isset($payload['form_ids'])) {
            $ids = array_map('intval', (array) $payload['form_ids']);
            foreach ($ids as $form_id) {
                if (!DynamicForm::lookup($form_id)) {
                    throw new Exception("Form not found: {$form_id}");
                }
            }
            // the core ticket form can never be detached (Topic::updateForms keeps type 'T')
            foreach (array_keys($stored_forms) as $form_id) {
                $form = DynamicForm::lookup($form_id);
                if ($form && $form->get('type') == 'T' && !in_array($form_id, $ids)) {
                    array_unshift($ids, $form_id);
                }
            }
            $vars = array_diff_key($vars, ['forms' => 1, 'fields' => 1]) + self::formVars($ids, $stored_forms);
        }
        return $vars;
    }

    private static function save(Topic $topic, array $vars) {
        FastApiModel::saveViaUpdate($topic, $vars, 'Help topic');
    }

    public static function create($payload, $requester_staff_id) {
        $defaults = [
            'topic' => '', 'topic_pid' => 0, 'dept_id' => 0, 'priority_id' => 0, 'status_id' => 0,
            'sla_id' => 0, 'page_id' => 0, 'ispublic' => 1, 'sequence_id' => 0,
            'number_format' => '', 'notes' => '', 'status' => 'active',
        ];
        $topic = Topic::create();
        self::save($topic, self::overlay($defaults, $payload));
        return ['topic_id' => $topic->getId(), 'topic' => $topic->topic];
    }

    public static function update($payload, $requester_staff_id) {
        $topic = self::requireTopic($payload['topic_id'] ?? 0);
        self::save($topic, self::overlay(self::storedVars($topic), $payload, self::storedForms($topic)));
        return ['topic_id' => $topic->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $topic = self::requireTopic($payload['topic_id'] ?? 0);
        if (!$topic->delete()) {
            throw new Exception('Help topic could not be deleted (it may still be in use).');
        }
        return ['topic_id' => (int) ($payload['topic_id'] ?? 0)];
    }
}


// ── SLAs ──────────────────────────────────────────────────────────────────────

/**
 * Mirrors osTicket's SLA page: name, grace_period (hours), is_active, schedule_id, transient,
 * disable_overdue_alerts, notes. SLA::update() rebuilds the whole flag bitmask from named keys,
 * so updates start from the stored flags; FLAG_ESCALATE (priority escalation, not on the page)
 * is carried over as stored.
 */
class FastApiSlaHandler {

    private static function requireSla($sla_id) {
        $sla = SLA::lookup((int) $sla_id);
        if (!$sla) {
            throw new Exception("SLA not found: {$sla_id}");
        }
        return $sla;
    }

    private static function varsFromFlags(array $vars, $flags) {
        foreach (['disable_overdue_alerts', 'transient', 'enable_priority_escalation'] as $key) {
            unset($vars[$key]);  // SLA::update() reads these with isset()
        }
        if ($flags & SLA::FLAG_NOALERTS)  $vars['disable_overdue_alerts'] = 1;
        if ($flags & SLA::FLAG_TRANSIENT) $vars['transient'] = 1;
        if ($flags & SLA::FLAG_ESCALATE)  $vars['enable_priority_escalation'] = SLA::FLAG_ESCALATE;
        return $vars;
    }

    private static function storedVars(SLA $sla) {
        $flags = (int) $sla->flags;
        $vars = [
            'id'           => $sla->getId(),
            'name'         => $sla->name,
            'grace_period' => $sla->grace_period,
            'schedule_id'  => $sla->schedule_id,
            'notes'        => $sla->notes,
            'isactive'     => ($flags & SLA::FLAG_ACTIVE) ? 1 : 0,
        ];
        return self::varsFromFlags($vars, $flags);
    }

    private static function overlay(array $vars, array $payload) {
        foreach (['name', 'grace_period', 'notes', 'schedule_id'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (!empty($vars['schedule_id']) && !Schedule::lookup((int) $vars['schedule_id'])) {
            throw new Exception('Schedule not found: ' . $vars['schedule_id']);
        }
        if (isset($payload['is_active'])) {
            $vars['isactive'] = (int) $payload['is_active'];
        }
        // checkboxes: present = on
        foreach (['transient', 'disable_overdue_alerts'] as $key) {
            if (isset($payload[$key])) {
                if ($payload[$key]) $vars[$key] = 1; else unset($vars[$key]);
            }
        }
        return $vars;
    }

    private static function save(SLA $sla, array $vars) {
        FastApiModel::saveViaUpdate($sla, $vars, 'SLA');
    }

    public static function create($payload, $requester_staff_id) {
        $defaults = ['name' => '', 'grace_period' => 0, 'schedule_id' => 0, 'notes' => '', 'isactive' => 1];
        $sla = SLA::create();
        self::save($sla, self::overlay($defaults, $payload));
        return ['sla_id' => $sla->getId(), 'name' => $sla->name];
    }

    public static function update($payload, $requester_staff_id) {
        $sla = self::requireSla($payload['sla_id'] ?? 0);
        self::save($sla, self::overlay(self::storedVars($sla), $payload));
        return ['sla_id' => $sla->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $sla = self::requireSla($payload['sla_id'] ?? 0);
        if (!$sla->delete()) {
            throw new Exception('SLA could not be deleted (it may still be in use).');
        }
        return ['sla_id' => (int) ($payload['sla_id'] ?? 0)];
    }
}


// ── Priorities ────────────────────────────────────────────────────────────────

/**
 * osTicket 1.18 has no admin UI or management API for priorities: Priority is a bare
 * model with getters. These handlers therefore write through the ORM directly and add
 * the safety osTicket itself never needed (it never deleted them): a priority that is
 * the system default, or that a help topic or ticket still references, is refused.
 */
class FastApiPriorityHandler {

    private static function requirePriority($priority_id) {
        $priority = Priority::lookup((int) $priority_id);
        if (!$priority) {
            throw new Exception("Priority not found: {$priority_id}");
        }
        return $priority;
    }

    private static function validate($name, $urgency) {
        if (trim($name) === '') {
            throw new Exception('A priority name is required.');
        }
        if ($urgency < 1) {
            throw new Exception('Urgency must be a positive integer (1 is the most urgent).');
        }
    }

    public static function create($payload, $requester_staff_id) {
        $name = trim($payload['name'] ?? '');
        $urgency = (int) ($payload['urgency'] ?? 2);
        self::validate($name, $urgency);
        if (Priority::objects()->filter(['priority' => $name])->exists()) {
            throw new Exception("A priority named '{$name}' already exists.");
        }

        $priority = new Priority([
            'priority'         => $name,
            'priority_desc'    => $payload['desc'] ?? $name,
            'priority_color'   => $payload['color'] ?? '#cccccc',
            'priority_urgency' => $urgency,
            'ispublic'         => (int) ($payload['is_public'] ?? 1),
        ]);
        if (!$priority->save() || !$priority->getId()) {
            throw new Exception('Priority could not be saved.');
        }
        return ['priority_id' => $priority->getId(), 'name' => $priority->getTag()];
    }

    public static function update($payload, $requester_staff_id) {
        $priority = self::requirePriority($payload['priority_id'] ?? 0);

        $name = isset($payload['name']) ? trim($payload['name']) : $priority->priority;
        $urgency = isset($payload['urgency']) ? (int) $payload['urgency'] : (int) $priority->priority_urgency;
        self::validate($name, $urgency);
        if ($name !== $priority->priority
                && Priority::objects()->filter(['priority' => $name])->exists()) {
            throw new Exception("A priority named '{$name}' already exists.");
        }

        $priority->priority = $name;
        $priority->priority_urgency = $urgency;
        if (isset($payload['desc']))      $priority->priority_desc = $payload['desc'];
        if (isset($payload['color']))     $priority->priority_color = $payload['color'];
        if (isset($payload['is_public'])) $priority->ispublic = (int) $payload['is_public'];
        if (!$priority->save()) {
            throw new Exception('Priority could not be updated.');
        }
        return ['priority_id' => $priority->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        global $cfg;
        $priority = self::requirePriority($payload['priority_id'] ?? 0);
        $id = $priority->getId();

        if ($cfg && $cfg->getDefaultPriorityId() == $id) {
            throw new Exception('The system default priority cannot be deleted.');
        }
        if (Topic::objects()->filter(['priority_id' => $id])->exists()) {
            throw new Exception('Priority is the default of a help topic; change the topic first.');
        }
        if (TicketCData::objects()->filter(['priority' => $id])->exists()) {
            throw new Exception('Priority is set on existing tickets and cannot be deleted.');
        }
        if (!$priority->delete()) {
            throw new Exception('Priority could not be deleted.');
        }
        return ['priority_id' => (int) $id];
    }
}
