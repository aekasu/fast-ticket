<?php
/**
 * Read-only handlers served by bridge/read.php. A handler returns an array (sent as {"result": ...}) or streams
 * bytes itself and returns null. It refuses by throwing FastApiReadRefused (status + code + message).
 */

require_once dirname(__FILE__) . '/common.php';

if (!class_exists('FastApiReadRefused')) {
class FastApiReadRefused extends Exception {
    public $status;
    public $code_name;
    function __construct($status, $code_name, $message) {
        parent::__construct($message);
        $this->status = $status;
        $this->code_name = $code_name;
    }
}
}

if (!class_exists('FastApiReadHandler')) {
class FastApiReadHandler {

    /**
     * A canned response with the ticket's variables filled in (%{ticket.name.first}, %{ticket.number}, ...): what
     * ajax.tickets.php cannedResponse() returns when an agent picks a premade reply. The agent must be able to open
     * the ticket; the canned response must exist and be enabled.
     * Params: canned_id, ticket_id, plain (bool: strip the HTML). Result: id, title, response, files.
     */
    public static function cannedRender($params, $staff) {
        global $thisstaff;
        $ticket = Ticket::lookup((int) ($params['ticket_id'] ?? 0));
        if (!$ticket || !$ticket->checkStaffPerm($thisstaff)) {
            throw new FastApiReadRefused(404, 'not_found', 'No such ticket.');
        }
        require_once INCLUDE_DIR . 'class.canned.php';
        $canned = Canned::lookup((int) ($params['canned_id'] ?? 0));
        if (!$canned || !$canned->isEnabled()) {
            throw new FastApiReadRefused(404, 'not_found', 'No such premade reply.');
        }
        $replace = function (&$var) use ($ticket) {
            return $ticket->replaceVars($var, ['recipient' => $ticket->getOwner()]);
        };
        $json = $canned->getFormattedResponse(!empty($params['plain']) ? 'json.plain' : 'json', $replace);
        $data = json_decode($json, true);
        if (!is_array($data)) {
            throw new FastApiReadRefused(500, 'error', 'The premade reply could not be rendered.');
        }
        $files = [];
        foreach ((array) ($data['files'] ?? []) as $f) {
            $files[] = ['id' => (int) $f['id'], 'name' => (string) $f['name'], 'size' => (int) $f['size']];
        }
        return ['id' => (int) $data['id'], 'title' => (string) $data['title'],
                'response' => (string) $data['response'], 'files' => $files];
    }

    /**
     * The bytes of a file attached to a thread entry of a ticket ('T') or task ('A'), read through osTicket's own
     * storage backend (database, filesystem, S3, ...). The attachment must belong to that object's thread and the agent
     * must be able to open the object. Streams the raw bytes; the API adds the file name and type.
     * Params: object_type, object_id, attachment_id.
     */
    public static function fileStream($params, $staff) {
        global $thisstaff;
        $type = (string) ($params['object_type'] ?? '');
        $attachment = Attachment::lookup((int) ($params['attachment_id'] ?? 0));
        $entry = ($attachment && $attachment->type === 'H') ? ThreadEntry::lookup((int) $attachment->object_id) : null;
        $thread = $entry ? $entry->getThread() : null;
        $object = $thread ? $thread->getObject() : null;
        if (!in_array($type, ['T', 'A'], true) || !$object || $thread->getObjectType() !== $type
                || (int) $thread->getObjectId() !== (int) ($params['object_id'] ?? 0)
                || !$object->checkStaffPerm($thisstaff)) {
            throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
        }
        $file = $attachment->getFile();
        if (!$file) {
            throw new FastApiReadRefused(404, 'not_found', 'No such file.');
        }
        try {
            $file->open();
        } catch (Throwable $e) {
            throw new FastApiReadRefused(501, 'file_unavailable', 'The file\'s storage backend cannot be read: ' . $e->getMessage());
        }
        header('Content-Type: application/octet-stream');
        header('Content-Length: ' . (int) $file->getSize());
        while (ob_get_level()) {
            ob_end_clean();
        }
        $file->sendData(false);
        return null;
    }
}
}
