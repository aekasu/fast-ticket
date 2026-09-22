<?php
/**
 * Lookup command handlers: Help Topics, SLAs, Priorities.
 *
 * Three classes in one file to keep related lookups together and avoid
 * a handler-file-per-class explosion for small domains.
 *
 * Payload field names match app/modules/lookups/models.py (TopicCreate,
 * SLACreate, PriorityCreate, and their *Update counterparts).
 */

require_once INCLUDE_DIR . 'class.topic.php';
require_once INCLUDE_DIR . 'class.sla.php';
require_once INCLUDE_DIR . 'class.priority.php';


// ── Help Topics ───────────────────────────────────────────────────────────────

class FastApiTopicHandler {

    private static function requireTopic($topic_id) {
        $topic = Topic::lookup((int) $topic_id);
        if (!$topic) {
            throw new Exception("Help topic not found: {$topic_id}");
        }
        return $topic;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'topic'      => $payload['topic'] ?? '',
            'ispublic'   => (int) ($payload['is_public'] ?? 1),
            'isactive'   => (int) ($payload['is_active'] ?? 1),
            'dept_id'    => $payload['dept_id'] ?? null,
            'sla_id'     => $payload['sla_id'] ?? null,
            'priority_id'=> $payload['priority_id'] ?? null,
        ];
        $errors = [];
        $topic = Topic::create($vars, $errors);
        if (!$topic) {
            throw new Exception('Topic::create failed: ' . implode(', ', $errors));
        }
        return ['topic_id' => $topic->getId(), 'topic' => $topic->getName()];
    }

    public static function update($payload, $requester_staff_id) {
        $topic = self::requireTopic($payload['topic_id'] ?? 0);
        $vars  = [];
        foreach (['topic', 'ispublic', 'isactive', 'dept_id', 'sla_id', 'priority_id'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (isset($payload['is_public']) && !isset($vars['ispublic'])) {
            $vars['ispublic'] = (int) $payload['is_public'];
        }
        if (isset($payload['is_active']) && !isset($vars['isactive'])) {
            $vars['isactive'] = (int) $payload['is_active'];
        }
        $errors = [];
        if (!$topic->update($vars, $errors)) {
            throw new Exception('Topic::update failed: ' . implode(', ', $errors));
        }
        return ['topic_id' => $topic->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $topic = self::requireTopic($payload['topic_id'] ?? 0);
        if (!$topic->delete()) {
            throw new Exception('Topic::delete() failed.');
        }
        return ['topic_id' => (int) ($payload['topic_id'] ?? 0)];
    }
}


// ── SLAs ──────────────────────────────────────────────────────────────────────

class FastApiSlaHandler {

    private static function requireSla($sla_id) {
        $sla = SLA::lookup((int) $sla_id);
        if (!$sla) {
            throw new Exception("SLA not found: {$sla_id}");
        }
        return $sla;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'name'         => $payload['name'] ?? '',
            'grace_period' => (int) ($payload['grace_period'] ?? 24),
            'isactive'     => (int) ($payload['is_active'] ?? 1),
            'flags'        => $payload['flags'] ?? 3, // default: enable+transient
        ];
        $errors = [];
        $sla = SLA::create($vars, $errors);
        if (!$sla) {
            throw new Exception('SLA::create failed: ' . implode(', ', $errors));
        }
        return ['sla_id' => $sla->getId(), 'name' => $sla->getName()];
    }

    public static function update($payload, $requester_staff_id) {
        $sla  = self::requireSla($payload['sla_id'] ?? 0);
        $vars = [];
        foreach (['name', 'grace_period', 'isactive', 'flags'] as $field) {
            if (isset($payload[$field])) {
                $vars[$field] = $payload[$field];
            }
        }
        if (isset($payload['is_active']) && !isset($vars['isactive'])) {
            $vars['isactive'] = (int) $payload['is_active'];
        }
        $errors = [];
        if (!$sla->update($vars, $errors)) {
            throw new Exception('SLA::update failed: ' . implode(', ', $errors));
        }
        return ['sla_id' => $sla->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $sla = self::requireSla($payload['sla_id'] ?? 0);
        if (!$sla->delete()) {
            throw new Exception('SLA::delete() failed.');
        }
        return ['sla_id' => (int) ($payload['sla_id'] ?? 0)];
    }
}


// ── Priorities ────────────────────────────────────────────────────────────────

class FastApiPriorityHandler {

    private static function requirePriority($priority_id) {
        $priority = Priority::lookup((int) $priority_id);
        if (!$priority) {
            throw new Exception("Priority not found: {$priority_id}");
        }
        return $priority;
    }

    public static function create($payload, $requester_staff_id) {
        $vars = [
            'priority'       => $payload['name'] ?? '',
            'priority_desc'  => $payload['desc'] ?? '',
            'priority_color' => $payload['color'] ?? '#cccccc',
            'ispublic'       => (int) ($payload['is_public'] ?? 1),
            'urgency'        => (int) ($payload['urgency'] ?? 2),
        ];
        $errors = [];
        $priority = Priority::create($vars, $errors);
        if (!$priority) {
            throw new Exception('Priority::create failed: ' . implode(', ', $errors));
        }
        return ['priority_id' => $priority->getId(), 'name' => $priority->getName()];
    }

    public static function update($payload, $requester_staff_id) {
        $priority = self::requirePriority($payload['priority_id'] ?? 0);
        $vars     = [];
        $fieldMap = [
            'name'    => 'priority',
            'desc'    => 'priority_desc',
            'color'   => 'priority_color',
            'is_public' => 'ispublic',
            'urgency' => 'urgency',
        ];
        foreach ($fieldMap as $pyKey => $phpKey) {
            if (isset($payload[$pyKey])) {
                $vars[$phpKey] = $payload[$pyKey];
            }
        }
        $errors = [];
        if (!$priority->update($vars, $errors)) {
            throw new Exception('Priority::update failed: ' . implode(', ', $errors));
        }
        return ['priority_id' => $priority->getId()];
    }

    public static function delete($payload, $requester_staff_id) {
        $priority = self::requirePriority($payload['priority_id'] ?? 0);
        if (!$priority->delete()) {
            throw new Exception('Priority::delete() failed.');
        }
        return ['priority_id' => (int) ($payload['priority_id'] ?? 0)];
    }
}
