<?php
/**
 * Authentication endpoint for the FastAPI integration bridge.
 *
 * POST /api/auth/integration   {"username": ..., "password": ...}
 * Returns the staff profile + permission set on valid credentials.
 *
 * Authentication goes through osTicket's own registered staff backends (StaffAuthenticationBackend),
 * the same ones the agent panel's login form uses, so an LDAP/SSO password backend keeps working and
 * osTicket's password policy applies. What it deliberately does NOT do is StaffAuthenticationBackend::
 * process()/login(): that writes $_SESSION and, for accounts with 2FA, emails a one-time code — neither
 * belongs in a stateless API.
 *
 * Every refusal carries a machine-readable "code" next to the message:
 *
 *   403 invalid_integration_key    the X-Integration-Key header does not match the plugin's key
 *   400 bad_request                username/password missing
 *   401 invalid_credentials        unknown user or wrong password
 *   401 account_disabled           correct password, but the account is locked/disabled
 *   403 two_factor_required        the account has 2FA, which the API cannot complete yet
 *   403 password_change_required   osTicket demands a password change (expired / policy / admin forced)
 *
 * Lockout on repeated failures is enforced by the API (per username + client address): osTicket's own
 * strike counter lives in the PHP session, which an API client can simply discard.
 */

// Bootstrap osTicket — we need the full osTicket context for the staff backends.
// Two ways in: through the plugin's `api` signal route (osTicket's api/http.php has already
// bootstrapped, INCLUDE_DIR is defined) or, for older installs that copied this file to
// api/auth/integration.php, standalone (bootstrap here; the webroot is three levels up).
if (!defined('INCLUDE_DIR')) {
    define('OSTAPI', TRUE);
    $__webroot = dirname(dirname(dirname(__FILE__)));
    define('API_SESSION', true);
    chdir($__webroot);
    require_once $__webroot . '/main.inc.php';
}
require_once INCLUDE_DIR . 'class.staff.php';
require_once INCLUDE_DIR . 'class.auth.php';

// This file is included from inside a method when served by the plugin's route, so the osTicket
// globals it needs must be declared here, not assumed.
global $ost;

if (!function_exists('fastapi_auth_refuse')) {
    function fastapi_auth_respond($status, $body) {
        http_response_code($status);
        header('Content-Type: application/json');
        echo json_encode($body);
        exit;
    }

    function fastapi_auth_refuse($status, $code, $message) {
        fastapi_auth_respond($status, ['error' => $message, 'code' => $code]);
    }
}

// ── Integration key validation ─────────────────────────────────────────────

// Same lookup as everywhere else (FastApiIntegrationPlugin::integrationKey()): the environment
// first, then the plugin's admin-panel setting, then the row install.php stored.
if (!class_exists('FastApiIntegrationPlugin'))
    require_once dirname(__FILE__) . '/plugin.php';
$expected_key = FastApiIntegrationPlugin::integrationKey();
$provided_key = $_SERVER['HTTP_X_INTEGRATION_KEY'] ?? '';

if (empty($expected_key) || !hash_equals($expected_key, $provided_key))
    fastapi_auth_refuse(403, 'invalid_integration_key', 'Invalid integration key.');

// ── Only accept POST ───────────────────────────────────────────────────────

if ($_SERVER['REQUEST_METHOD'] !== 'POST')
    fastapi_auth_refuse(405, 'method_not_allowed', 'Method not allowed.');

// ── Parse request body ─────────────────────────────────────────────────────

$body = json_decode(file_get_contents('php://input'), true);
$username = isset($body['username']) && is_string($body['username']) ? trim($body['username']) : '';
$password = isset($body['password']) && is_string($body['password']) ? substr($body['password'], 0, 128) : '';

if (!$username || !$password)
    fastapi_auth_refuse(400, 'bad_request', 'username and password are required.');

// ── Authenticate through osTicket's staff backends ─────────────────────────

$allowed = StaffAuthenticationBackend::getAllowedBackends($username);
$result = null;
$used_backend = null;
foreach (StaffAuthenticationBackend::allRegistered() as $bk) {
    // Lockout/audit backends and token-based sign-on don't authenticate a username + password.
    if (!$bk->supportsInteractiveAuthentication())
        continue;
    if ($allowed && !in_array($bk->getBkId(), $allowed))
        continue;   // this user is bound to a different backend
    try {
        $attempt = $bk->authenticate($username, $password);
    } catch (AccessDenied $e) {
        $result = $e;
        break;
    }
    if ($attempt instanceof AccessDenied) {
        $result = $attempt;
        break;
    }
    if ($attempt instanceof AuthenticatedUser) {
        $result = $attempt;
        $used_backend = $bk;
        break;
    }
}

$staff = ($result instanceof AuthenticatedUser) ? Staff::lookup((int) $result->getId()) : null;
$client_ip = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? '?';

if (!$staff) {
    // The trail osTicket leaves for a failed agent login: the auth.login.failed signal, the
    // backends' audit hooks, and a System Logs warning (no alert email — the API throttles).
    $info = ['username' => $username, 'password' => $password];
    $denied = ($result instanceof AccessDenied) ? $result : new AccessDenied(__('Access denied'));
    try {
        Signal::send('auth.login.failed', null, $info);
        StaffAuthenticationBackend::authAudit($denied, $info);
    } catch (Throwable $e) {
        error_log('[FastAPI Integration] login audit failed: ' . $e->getMessage());
    }
    $ost->logWarning(
        sprintf(_S('Failed agent login attempt (%s)'), $username),
        sprintf("%s: %s\n%s: %s\n%s: %s", _S('Username'), $username, _S('IP'), $client_ip,
            _S('Via'), 'fast-ticket API'),
        false);
    fastapi_auth_refuse(401, 'invalid_credentials', 'Invalid username or password.');
}

// ── The password was right. Is this account allowed to use the API? ────────

if (!$staff->isActive())
    fastapi_auth_refuse(401, 'account_disabled', 'This account is locked or disabled.');

// force2faConfig(): the organization requires 2FA and this agent has not enrolled — the agent panel
// would make them set it up before letting them in, so the API must not be the way around it.
if ($staff->get2FABackend() || $staff->force2faConfig())
    fastapi_auth_refuse(403, 'two_factor_required',
        'This account has two-factor authentication enabled, which the API does not support yet. '
        . 'Use an account without 2FA for API access, or sign in through the osTicket agent panel.');

if ($staff->forcePasswdChange())
    fastapi_auth_refuse(403, 'password_change_required',
        'osTicket requires this account to change its password (it is expired, does not meet the '
        . 'password policy, or an administrator forced a change). Sign in through the osTicket agent '
        . 'panel to set a new one.');

// ── Record the login the way the agent panel does (minus the session) ───────

try {
    $ost->logDebug(_S('Agent Login'),
        sprintf(_S("%s logged in [%s], via %s"), $staff->getUserName(), $client_ip, 'fast-ticket API'));
    Signal::send('person.login', $staff, ['type' => 'login']);
    Signal::send('auth.login.succeeded', $staff);
    $staff->onLogin($used_backend);   // last login time
} catch (Throwable $e) {
    error_log('[FastAPI Integration] login bookkeeping failed: ' . $e->getMessage());
}

// ── Return the staff identity ──────────────────────────────────────────────
// No permissions here: the API reads what an agent may do from osTicket's tables on every request.

fastapi_auth_respond(200, [
    'staff_id'   => (int) $staff->getId(),
    'username'   => $staff->getUsername(),
    'firstname'  => $staff->getFirstName(),
    'lastname'   => $staff->getLastName(),
    'email'      => $staff->getEmail(),
    'is_admin'   => (bool) $staff->isAdmin(),
]);
