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
        if (in_array($type, ['F', 'C'], true)) {
            self::requireKbAttachment($type, $attachment, (int) ($params['object_id'] ?? 0));
        } else {
            $entry = ($attachment && $attachment->type === 'H') ? ThreadEntry::lookup((int) $attachment->object_id) : null;
            $thread = $entry ? $entry->getThread() : null;
            $object = $thread ? $thread->getObject() : null;
            if (!in_array($type, ['T', 'A'], true) || !$object || $thread->getObjectType() !== $type
                    || (int) $thread->getObjectId() !== (int) ($params['object_id'] ?? 0)
                    || !$object->checkStaffPerm($thisstaff)) {
                throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
            }
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

    /**
     * A file attached to a FAQ ('F': any agent may read the knowledge base) or to a canned response ('C': the reply
     * picker's rule, ``Canned::getCannedResponses`` — enabled and global or of one of the agent's departments — or the
     * management page's, ``canned.manage`` in the response's department).
     */
    private static function requireKbAttachment($type, $attachment, $object_id) {
        global $thisstaff;
        if (!$attachment || $attachment->type !== $type || (int) $attachment->object_id !== $object_id || $attachment->inline) {
            throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
        }
        if ($type === 'F') {
            if (!FAQ::lookup($object_id)) {
                throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
            }
            return;
        }
        require_once INCLUDE_DIR . 'class.canned.php';
        $canned = Canned::lookup($object_id);
        if (!$canned) {
            throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
        }
        $dept = (int) $canned->getDeptId();
        $manages = $dept ? ($thisstaff->getRole($dept) && $thisstaff->getRole($dept)->hasPerm(Canned::PERM_MANAGE)) : $thisstaff->hasPerm(Canned::PERM_MANAGE, false);
        $picks = $canned->isEnabled() && (!$dept || in_array($dept, $thisstaff->getDepts()));
        if (!$manages && !$picks) {
            throw new FastApiReadRefused(404, 'not_found', 'No such attachment.');
        }
    }

    /**
     * What the Admin Panel's "System Information" page shows (scp/system.php). Administrators only.
     * Result: osticket_version, web_server, mysql_version, php_version, extensions[], php_settings[], schema_*,
     * space_used_mib, attachment_space_mib, database_timezone, interpreted_timezone, language_packs[].
     */
    public static function systemInfo($params, $staff) {
        global $thisstaff, $cfg, $ost;
        if (!$thisstaff || !$thisstaff->isAdmin()) {
            throw new FastApiReadRefused(404, 'not_found', 'No such page.');
        }
        $known = [
            'gd' => ['gdlib', 'Used for image manipulation and PDF printing'],
            'imap' => ['imap', 'Used for email fetching'],
            'xml' => ['xml', 'XML API'],
            'dom' => ['xml-dom', 'Used for HTML email processing'],
            'json' => ['json', 'Improves performance creating and processing JSON'],
            'mbstring' => ['mbstring', 'Highly recommended for non western european language content'],
            'phar' => ['phar', 'Highly recommended for plugins and language packs'],
            'intl' => ['intl', 'Highly recommended for non western european language content'],
            'fileinfo' => ['fileinfo', 'Used to detect file types for uploads'],
            'zip' => ['zip', 'Used for ticket and task exporting'],
            'apcu' => ['APCu', 'Improves overall performance'],
            'Zend Opcache' => ['Zend Opcache', 'Improves overall performance'],
        ];
        $extensions = [];
        foreach ($known as $key => $info) {
            $extensions[] = ['key' => $key, 'name' => $info[0], 'description' => $info[1], 'loaded' => extension_loaded($key)];
        }
        $space = function ($sql) {
            return round((float) db_result(db_query($sql)), 2);
        };
        $packs = [];
        foreach (Internationalization::availableLanguages() as $info) {
            $p = $info['path'];
            if (!empty($info['phar'])) {
                $p = 'phar://' . $p;
            }
            $manifest = file_exists($p . '/MANIFEST.php') ? (include $p . '/MANIFEST.php') : null;
            $packs[] = ['code' => (string) $info['code'],
                        'name' => (string) Internationalization::getLanguageDescription($info['code']),
                        'version' => $manifest ? (string) ($manifest['Version'] ?? '') : null];
        }
        return [
            'osticket_version' => (string) $ost->getVersion(),
            'web_server' => (string) ($_SERVER['SERVER_SOFTWARE'] ?? ''),
            'mysql_version' => (string) db_version(),
            'php_version' => phpversion(),
            'extensions' => $extensions,
            'php_settings' => [
                ['name' => 'cgi.fix_pathinfo', 'value' => (string) ini_get('cgi.fix_pathinfo'), 'ok' => ini_get('cgi.fix_pathinfo') == 1],
                ['name' => 'date.timezone', 'value' => (string) ini_get('date.timezone'), 'ok' => (bool) ini_get('date.timezone')],
            ],
            'schema_name' => DBNAME,
            'schema_signature' => (string) $cfg->getSchemaSignature(),
            'space_used_mib' => $space('SELECT sum(data_length + index_length) / 1048576 FROM information_schema.TABLES WHERE table_schema = ' . db_input(DBNAME)),
            'attachment_space_mib' => $space('SELECT (DATA_LENGTH + INDEX_LENGTH) / 1048576 FROM information_schema.TABLES WHERE TABLE_SCHEMA = ' . db_input(DBNAME) . ' AND TABLE_NAME = ' . db_input(FILE_CHUNK_TABLE)),
            'database_timezone' => (string) db_timezone(),
            'interpreted_timezone' => (string) $cfg->getDbTimezone(),
            'language_packs' => $packs,
        ];
    }

    /**
     * The Dashboard activity report (scp/dashboard.php): OverviewReport's plot and one tabular group, which applies
     * osTicket's own scoping to the signed-in agent. Params: start ('' = a month ago, else YYYY-MM-DD), period (one of
     * OverviewReport::$end_choices keys), group (dept | topic | staff).
     */
    public static function dashboardReport($params, $staff) {
        global $thisstaff;
        require_once INCLUDE_DIR . 'class.report.php';
        $period = (string) ($params['period'] ?? 'now');
        $group = (string) ($params['group'] ?? 'dept');
        $start = (string) ($params['start'] ?? '');
        if (!array_key_exists($period, OverviewReport::$end_choices)
                || !in_array($group, ['dept', 'topic', 'staff'], true)
                || ($start !== '' && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $start))) {
            throw new FastApiReadRefused(400, 'bad_request', 'Bad report parameters.');
        }
        $report = new OverviewReport($start, $period);
        $plot = $report->getPlotData();
        $days = array_map(function ($t) { return date('Y-m-d', $t); }, $plot['times']);
        $series = [];
        foreach ($plot['plots'] as $event => $counts) {
            $series[] = ['event' => (string) $event, 'counts' => array_map('intval', $counts)];
        }
        $table = $report->getTabularData($group);
        $rows = array_map(function ($row) { return array_map('strval', $row); }, $table['data']);
        return [
            'plot' => ['days' => $days, 'series' => $series],
            'table' => ['columns' => array_map('strval', $table['columns']), 'rows' => $rows],
        ];
    }
}
}
