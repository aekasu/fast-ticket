<?php
/**
 * Authentication endpoint for the FastAPI integration bridge.
 *
 * Called by FastAPI at POST /api/auth/integration
 * Returns staff profile + permission set on valid credentials.
 *
 * Security:
 *   - Request must include X-Integration-Key header matching the plugin config.
 *   - Credentials are validated against osTicket's native Staff model.
 *   - Returns HTTP 401 on bad credentials, 403 on bad integration key.
 */

// Bootstrap osTicket — we need the full osTicket context for Staff::lookup
define('OSTAPI', TRUE);
require_once dirname(dirname(dirname(dirname(__FILE__)))) . '/bootstrap.php';
require_once INCLUDE_DIR . 'api.inc.php';
require_once INCLUDE_DIR . 'class.staff.php';

// ── Integration key validation ─────────────────────────────────────────────

$expected_key = $_SERVER['FASTAPI_INTEGRATION_KEY'] ?? '';
$provided_key = $_SERVER['HTTP_X_INTEGRATION_KEY'] ?? '';

if (empty($expected_key) || !hash_equals($expected_key, $provided_key)) {
    http_response_code(403);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Invalid integration key.']);
    exit;
}

// ── Only accept POST ───────────────────────────────────────────────────────

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Method not allowed.']);
    exit;
}

// ── Parse request body ─────────────────────────────────────────────────────

$body = json_decode(file_get_contents('php://input'), true);
$username = isset($body['username']) ? trim($body['username']) : '';
$password = $body['password'] ?? '';

if (!$username || !$password) {
    http_response_code(400);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'username and password are required.']);
    exit;
}

// ── Authenticate against osTicket Staff model ──────────────────────────────

$staff = Staff::lookup(['username' => $username]);
if (!$staff || !$staff->check_passwd($password)) {
    http_response_code(401);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Invalid username or password.']);
    exit;
}

if (!$staff->isActive()) {
    http_response_code(401);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'Staff account is not active.']);
    exit;
}

// ── Build permission list ──────────────────────────────────────────────────

$permissions = [];
$is_admin = (bool) $staff->isAdmin();

if (!$is_admin) {
    // Fetch role permissions from ost_role
    $role_id = $staff->get('role_id');
    if ($role_id) {
        $role_result = db_query("SELECT permissions FROM ost_role WHERE id=" . db_input($role_id));
        if ($role_row = db_fetch_array($role_result)) {
            $perm_map = json_decode($role_row['permissions'], true) ?: [];
            foreach ($perm_map as $key => $val) {
                if ($val) {
                    $permissions[] = $key;
                }
            }
        }
    }
}

// ── Return staff profile ───────────────────────────────────────────────────

http_response_code(200);
header('Content-Type: application/json');
echo json_encode([
    'staff_id'   => (int) $staff->getId(),
    'username'   => $staff->getUsername(),
    'firstname'  => $staff->getFirstName(),
    'lastname'   => $staff->getLastName(),
    'email'      => $staff->getEmail(),
    'is_admin'   => $is_admin,
    'permissions'=> $permissions,
]);
