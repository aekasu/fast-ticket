<?php
/**
 * Site pages (scp/pages.php) through Page::update(), which validates and rewrites every field — so an update starts
 * from the stored values. A page in use (a default page, or a help topic's) cannot be disabled or deleted.
 */
include_once INCLUDE_DIR . 'class.page.php';

if (!class_exists('FastApiPageHandler')) {

class FastApiPageHandler {

    private static function requirePage($page_id) {
        $page = Page::lookup((int) $page_id);
        if (!$page || !in_array($page->getType(), ['other', 'landing', 'thank-you', 'offline'], true)) {
            throw new Exception("Page not found: {$page_id}");
        }
        return $page;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    public static function create($payload, $staff_id) {
        $page = Page::create();
        $errors = [];
        $vars = [
            'name'     => (string) ($payload['name'] ?? ''),
            'type'     => (string) ($payload['type'] ?? ''),
            'body'     => (string) ($payload['body'] ?? ''),
            'isactive' => (int) !empty($payload['is_active']),
            'notes'    => (string) ($payload['notes'] ?? ''),
        ];
        if (!$page->update($vars, $errors) || $errors || !$page->getId()) {
            throw new Exception('Page was not created: ' . self::errorText($errors ?: ['nothing was stored']));
        }
        return ['page_id' => $page->getId(), 'name' => $page->getName()];
    }

    public static function update($payload, $staff_id) {
        $page = self::requirePage($payload['page_id'] ?? 0);
        $vars = [
            'id'       => $page->getId(),
            'name'     => array_key_exists('name', $payload) ? (string) $payload['name'] : $page->getName(),
            'type'     => array_key_exists('type', $payload) ? (string) $payload['type'] : $page->getType(),
            'body'     => array_key_exists('body', $payload) ? (string) $payload['body'] : $page->getBody(),
            'isactive' => array_key_exists('is_active', $payload) ? (int) !empty($payload['is_active']) : (int) $page->isActive(),
            'notes'    => array_key_exists('notes', $payload) ? (string) $payload['notes'] : (string) $page->getNotes(),
        ];
        $errors = [];
        if (!$page->update($vars, $errors) || $errors) {
            throw new Exception('Page was not updated: ' . self::errorText($errors ?: ['unknown error']));
        }
        return ['page_id' => $page->getId()];
    }

    public static function delete($payload, $staff_id) {
        $page = self::requirePage($payload['page_id'] ?? 0);
        $id = $page->getId();
        if ($page->isInUse()) {
            throw new Exception('A page currently in use cannot be deleted.');
        }
        if (!$page->delete()) {
            throw new Exception('Page could not be deleted.');
        }
        return ['page_id' => $id];
    }
}
}
