<?php
/**
 * Custom lists (scp/lists.php) through DynamicList::add()/update()/delete() and DynamicListItem::update(). The page
 * rewrites the fields it knows, so updates start from the stored values. Lists osTicket manages itself (ticket
 * statuses: a list with a `type`) are refused. Deleting an item only un-links it from the list, as osTicket does.
 */
include_once INCLUDE_DIR . 'class.list.php';

if (!class_exists('FastApiListHandler')) {

class FastApiListHandler {

    private static function requireList($list_id) {
        $list = DynamicList::lookup((int) $list_id);
        if (!$list) {
            throw new Exception("List not found: {$list_id}");
        }
        if ($list->type) {
            throw new Exception("'{$list->getName()}' is managed by osTicket and cannot be changed here.");
        }
        return $list;
    }

    private static function requireItem(DynamicList $list, $item_id) {
        $item = DynamicListItem::lookup((int) $item_id);
        if (!$item || (int) $item->getListId() !== (int) $list->getId()) {
            throw new Exception("List item not found: {$item_id}");
        }
        return $item;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function create($payload, $staff_id) {
        $errors = [];
        $vars = [
            'name'        => (string) ($payload['name'] ?? ''),
            'name_plural' => (string) ($payload['name_plural'] ?? ''),
            'sort_mode'   => (string) ($payload['sort_mode'] ?? 'Alpha'),
            'notes'       => (string) ($payload['notes'] ?? ''),
        ];
        $list = DynamicList::add($vars, $errors);
        if (!$list || $errors || !$list->getId()) {
            throw new Exception('List was not created: ' . self::errorText($errors ?: ['nothing was stored']));
        }
        return ['list_id' => $list->getId(), 'name' => $list->getName()];
    }

    public static function update($payload, $staff_id) {
        $list = self::requireList($payload['list_id'] ?? 0);
        $vars = [];
        foreach (['name', 'name_plural', 'sort_mode', 'notes'] as $key) {
            $vars[$key] = array_key_exists($key, $payload) ? (string) $payload[$key] : (string) $list->get($key);
        }
        $errors = [];
        if (!$list->update($vars, $errors) || $errors) {
            throw new Exception('List was not updated: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['list_id' => $list->getId()];
    }

    public static function delete($payload, $staff_id) {
        $list = self::requireList($payload['list_id'] ?? 0);
        $id = $list->getId();
        if (!$list->isDeleteable()) {
            throw new Exception('This list cannot be deleted.');
        }
        if (!$list->delete()) {
            throw new Exception('List could not be deleted (a form field may still use it).');
        }
        return ['list_id' => $id];
    }

    public static function createItem($payload, $staff_id) {
        $list = self::requireList($payload['list_id'] ?? 0);
        if (!$list->allowAdd()) {
            throw new Exception('Items cannot be added to this list.');
        }
        $value = trim((string) ($payload['value'] ?? ''));
        if ($value === '') {
            throw new Exception("A list item 'value' is required.");
        }
        if (!$list->isItemUnique(['value' => $value])) {
            throw new Exception("The list already has an item '{$value}'.");
        }
        $errors = [];
        $item = $list->addItem([
            'value' => $value,
            'extra' => (string) ($payload['abbrev'] ?? ''),
            'sort'  => (int) ($payload['sort'] ?? 1),
        ], $errors);
        if (!$item) {
            throw new Exception('List item was not created: ' . self::errorText($errors ?: ['unknown error']));
        }
        if (empty($payload['is_enabled']) && array_key_exists('is_enabled', $payload)) {
            $item->disable();
        } else {
            $item->enable();
        }
        if (!$item->save() || !$item->getId()) {
            throw new Exception('List item was not stored.');
        }
        return ['item_id' => $item->getId(), 'list_id' => $list->getId()];
    }

    public static function updateItem($payload, $staff_id) {
        $list = self::requireList($payload['list_id'] ?? 0);
        $item = self::requireItem($list, $payload['item_id'] ?? 0);
        $vars = [
            'value'  => array_key_exists('value', $payload) ? (string) $payload['value'] : $item->get('value'),
            'abbrev' => array_key_exists('abbrev', $payload) ? (string) $payload['abbrev'] : (string) $item->get('extra'),
            'sort'   => array_key_exists('sort', $payload) ? (int) $payload['sort'] : (int) $item->get('sort'),
        ];
        $errors = [];
        if (!$item->update($vars, $errors) || $errors) {
            throw new Exception('List item was not updated: ' . self::errorText($errors ?: ['unknown error']));
        }
        if (array_key_exists('is_enabled', $payload)) {
            if ($payload['is_enabled']) {
                $item->enable();
            } elseif ($item->isDisableable()) {
                $item->disable();
            } else {
                throw new Exception('This item is internal and cannot be disabled.');
            }
            $item->save();
        }
        return ['item_id' => $item->getId()];
    }

    public static function deleteItem($payload, $staff_id) {
        $list = self::requireList($payload['list_id'] ?? 0);
        $item = self::requireItem($list, $payload['item_id'] ?? 0);
        if (!$item->isDeletable()) {
            throw new Exception('This item is internal and cannot be deleted.');
        }
        $id = $item->getId();
        if (!$item->delete()) {
            throw new Exception('List item could not be removed.');
        }
        return ['item_id' => $id];
    }
}
}
