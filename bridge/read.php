<?php
/**
 * Read endpoint of the FastAPI integration bridge.
 *
 * POST /api/fast-ticket/read   {"op": "canned.render", "staff_id": 3, "params": {...}}
 *
 * Needs the integration key (X-Integration-Key), like login and the handshake. Runs the named read (reads.php) as
 * the given active staff member, so osTicket's own permission checks apply, and answers {"result": ...} or streams
 * bytes. Reads never write. Every refusal is {"error": message, "code": code}:
 *   403 invalid_integration_key · 400 bad_request · 404 not_found · 501 file_unavailable · 500 error
 */

global $ost, $thisstaff;

if (!function_exists('fastapi_read_respond')) {
    function fastapi_read_respond($status, $body) {
        while (ob_get_level()) {
            ob_end_clean();
        }
        http_response_code($status);
        header('Content-Type: application/json');
        echo json_encode($body);
        exit;
    }
}

if (!class_exists('FastApiIntegrationPlugin'))
    require_once dirname(__FILE__) . '/plugin.php';
$expected_key = FastApiIntegrationPlugin::integrationKey();
if (empty($expected_key) || !hash_equals($expected_key, $_SERVER['HTTP_X_INTEGRATION_KEY'] ?? '')) {
    fastapi_read_respond(403, ['error' => 'Invalid integration key.', 'code' => 'invalid_integration_key']);
}
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    fastapi_read_respond(405, ['error' => 'Method not allowed.', 'code' => 'method_not_allowed']);
}

$request = json_decode(file_get_contents('php://input'), true);
$op = is_array($request) && isset($request['op']) && is_string($request['op']) ? $request['op'] : '';
$params = is_array($request) && isset($request['params']) && is_array($request['params']) ? $request['params'] : [];
$reads = require dirname(__FILE__) . '/reads.php';
if (!isset($reads[$op])) {
    fastapi_read_respond(400, ['error' => "Unknown read: {$op}", 'code' => 'bad_request']);
}

require_once INCLUDE_DIR . 'class.staff.php';
if (class_exists('ModelInstanceManager'))
    ModelInstanceManager::flushCache();
$thisstaff = Staff::lookup((int) ($request['staff_id'] ?? 0));
if (!$thisstaff || !$thisstaff->isActive()) {
    fastapi_read_respond(403, ['error' => 'The requesting staff member does not exist or is not active.', 'code' => 'forbidden']);
}

list($file, $class, $method) = $reads[$op];
require_once dirname(__FILE__) . "/handlers/{$file}.php";
try {
    $result = $class::$method($params, $thisstaff);
} catch (FastApiReadRefused $e) {
    fastapi_read_respond($e->status, ['error' => $e->getMessage(), 'code' => $e->code_name]);
} catch (Throwable $e) {
    error_log("[FastAPI Integration] read {$op} failed: " . $e->getMessage());
    fastapi_read_respond(500, ['error' => $e->getMessage(), 'code' => 'error']);
}
if ($result !== null) {
    fastapi_read_respond(200, ['result' => $result]);
}
exit;   // the handler streamed its bytes
