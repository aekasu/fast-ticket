<?php
/**
 * Ticket filters (scp/filters.php) through Filter::update(): name / order / target / flags, and the rules, which osTicket
 * replaces as a whole. Actions are replaced as a whole too, but written here rather than through the page's
 * "N/I/D" action codes: each action's configuration is parsed and validated by osTicket's own action form
 * (FilterAction::setConfiguration) before anything is saved. The system ban list has its own API and is refused.
 */
include_once INCLUDE_DIR . 'class.filter.php';
include_once INCLUDE_DIR . 'class.filter_action.php';

if (!class_exists('FastApiFilterHandler')) {

class FastApiFilterHandler {

    /** API field => the name osTicket's action form reads it under */
    const CONFIG_NAMES = [
        'canned_id' => 'canned_id', 'dept_id' => 'dept_id', 'priority' => 'priority', 'sla_id' => 'sla_id',
        'team_id' => 'team_id', 'staff_id' => 'staff_id', 'topic_id' => 'topic_id', 'status_id' => 'status_id',
        'recipients' => 'recipients', 'subject' => 'subject', 'message' => 'message', 'from' => 'from',
    ];

    private static function requireFilter($filter_id) {
        $filter = Filter::lookup((int) $filter_id);
        if (!$filter || $filter->isSystemBanlist()) {
            throw new Exception("Filter not found: {$filter_id}");
        }
        return $filter;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map(function ($e) { return is_array($e) ? implode(', ', $e) : (string) $e; }, $errors));
    }

    /** Parse and validate every action's configuration; returns [[type, config-json], ...] ready to be saved. */
    private static function checkActions(array $actions) {
        $checked = [];
        $seen = [];
        foreach (array_values($actions) as $i => $info) {
            $type = (string) ($info['type'] ?? '');
            $impl = FilterAction::lookupByType($type);
            if (!$impl) {
                throw new Exception("Unknown filter action type: {$type}");
            }
            if (isset($seen[$type]) && !($impl::$flags & TriggerAction::FLAG_MULTI_USE)) {
                throw new Exception("The '{$type}' action can be used once per filter.");
            }
            $seen[$type] = true;
            $source = [];
            foreach (self::CONFIG_NAMES as $key => $name) {
                if (array_key_exists($key, $info)) {
                    $source[$name] = $info[$key];
                }
            }
            $action = new FilterAction(['type' => $type, 'sort' => $i + 1]);
            $errors = [];
            if (!$action->setConfiguration($errors, $source) || $errors) {
                throw new Exception("Action '{$type}': " . self::errorText($errors ?: ['invalid configuration']));
            }
            $config = json_decode($action->get('configuration'), true) ?: [];
            if ($type === 'dept' && (!($d = Dept::lookup((int) ($config['dept_id'] ?? 0))) || !$d->isActive())) {
                throw new Exception('Unable to save: Please choose an active Department');
            }
            if ($type === 'topic' && (!($t = Topic::lookup((int) ($config['topic_id'] ?? 0))) || !$t->isActive())) {
                throw new Exception('Unable to save: Please choose an active Help Topic');
            }
            $checked[] = ['type' => $type, 'sort' => $i + 1, 'configuration' => $action->get('configuration')];
        }
        return $checked;
    }

    private static function saveActions(Filter $filter, array $checked) {
        FilterAction::objects()->filter(['filter_id' => $filter->getId()])->delete();
        foreach ($checked as $c) {
            $action = new FilterAction(['type' => $c['type'], 'filter_id' => $filter->getId(), 'sort' => $c['sort']]);
            $action->set('configuration', $c['configuration']);
            if (!$action->save()) {
                throw new Exception("Action '{$c['type']}' could not be saved.");
            }
        }
    }

    private static function rulesVars(array $rules) {
        return array_map(function ($r) {
            return ['w' => (string) ($r['what'] ?? ''), 'h' => (string) ($r['how'] ?? ''), 'v' => (string) ($r['val'] ?? '')];
        }, array_values($rules));
    }

    private static function write(Filter $filter, array $vars) {
        $errors = [];
        if (!$filter->update($vars, $errors) || $errors) {
            throw new Exception('Filter was not saved: ' . self::errorText($errors ?: ['unknown error']));
        }
    }

    public static function create($payload, $staff_id) {
        $checked = self::checkActions((array) ($payload['actions'] ?? []));
        $target = (string) ($payload['target'] ?? 'Any');
        $vars = [
            'pass'            => 1,   // Filter::update() refuses a save that carries no 'actions' array unless this is set
            'name'            => (string) ($payload['name'] ?? ''),
            'execorder'       => (int) ($payload['execorder'] ?? 99),
            'isactive'        => (int) (array_key_exists('is_active', $payload) ? !empty($payload['is_active']) : true),
            'target'          => ($target === 'Email' && !empty($payload['email_id'])) ? (int) $payload['email_id'] : $target,
            'match_all_rules' => (int) !empty($payload['match_all_rules']),
            'stop_onmatch'    => (int) !empty($payload['stop_onmatch']),
            'notes'           => (string) ($payload['notes'] ?? ''),
            'rules'           => self::rulesVars((array) ($payload['rules'] ?? [])),
        ];
        $filter = new Filter();
        self::write($filter, $vars);
        if (!$filter->getId()) {
            throw new Exception('Filter was not stored.');
        }
        self::saveActions($filter, $checked);
        return ['filter_id' => $filter->getId(), 'name' => $filter->getName()];
    }

    public static function update($payload, $staff_id) {
        $filter = self::requireFilter($payload['filter_id'] ?? 0);
        $checked = array_key_exists('actions', $payload) ? self::checkActions((array) $payload['actions']) : null;
        $target = array_key_exists('target', $payload) ? (string) $payload['target'] : (string) $filter->getTarget();
        $email_id = array_key_exists('email_id', $payload) ? (int) $payload['email_id'] : (int) $filter->getEmailId();
        $flag = function ($key, $stored) use ($payload) {
            return (int) (array_key_exists($key, $payload) ? !empty($payload[$key]) : $stored);
        };
        $vars = [
            'pass'            => 1,
            'name'            => array_key_exists('name', $payload) ? (string) $payload['name'] : $filter->getName(),
            'execorder'       => array_key_exists('execorder', $payload) ? (int) $payload['execorder'] : (int) $filter->getExecOrder(),
            'isactive'        => $flag('is_active', $filter->isActive()),
            'target'          => ($target === 'Email' && $email_id) ? $email_id : $target,
            'match_all_rules' => $flag('match_all_rules', $filter->matchAllRules()),
            'stop_onmatch'    => $flag('stop_onmatch', $filter->stopOnMatch()),
            'notes'           => array_key_exists('notes', $payload) ? (string) $payload['notes'] : (string) $filter->getNotes(),
            'rules'           => array_key_exists('rules', $payload) ? self::rulesVars((array) $payload['rules']) : $filter->getRules(),
        ];
        self::write($filter, $vars);
        if ($checked !== null) {
            self::saveActions($filter, $checked);
        }
        return ['filter_id' => $filter->getId()];
    }

    public static function delete($payload, $staff_id) {
        $filter = self::requireFilter($payload['filter_id'] ?? 0);
        $id = $filter->getId();
        if (!$filter->delete()) {
            throw new Exception('Filter could not be deleted.');
        }
        return ['filter_id' => $id];
    }
}
}
