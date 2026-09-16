<?php
/**
 * User command handlers.
 */

require_once INCLUDE_DIR . 'class.user.php';
require_once INCLUDE_DIR . 'class.organization.php';

class FastApiUserHandler {

    private static function requireUser($user_id) {
        $user = User::lookup((int) $user_id);
        if (!$user) {
            throw new Exception("User not found: {$user_id}");
        }
        return $user;
    }

    public static function create($payload, $staff_id) {
        $vars = [
            'name'   => $payload['name'] ?? '',
            'email'  => $payload['email'] ?? '',
            'phone'  => $payload['phone'] ?? '',
            'org_id' => $payload['org_id'] ?? null,
        ];
        $errors = [];
        $user = User::fromVars($vars, $errors);
        if (!$user) {
            throw new Exception('User::create failed: ' . implode(', ', $errors));
        }
        return [
            'user_id' => $user->getId(),
            'name'    => $user->getName()->getFull(),
            'email'   => $user->getDefaultEmailAddress(),
        ];
    }

    public static function update($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        $vars = [];
        if (isset($payload['name']))  $vars['name']  = $payload['name'];
        if (isset($payload['phone'])) $vars['phone'] = $payload['phone'];
        $errors = [];
        if (!$user->updateInfo($vars, $errors)) {
            throw new Exception('User::updateInfo failed: ' . implode(', ', $errors));
        }
        return ['user_id' => $user->getId()];
    }

    public static function setOrg($payload, $staff_id) {
        $user   = self::requireUser($payload['user_id'] ?? 0);
        $org_id = (int) ($payload['org_id'] ?? 0);

        if ($org_id === 0) {
            // Remove from org
            $user->setOrganization(null);
        } else {
            $org = Organization::lookup($org_id);
            if (!$org) {
                throw new Exception("Organization not found: {$org_id}");
            }
            $user->setOrganization($org);
        }
        return ['user_id' => $user->getId(), 'org_id' => $org_id ?: null];
    }

    public static function delete($payload, $staff_id) {
        $user = self::requireUser($payload['user_id'] ?? 0);
        if (!$user->delete()) {
            throw new Exception('User::delete() failed.');
        }
        return ['user_id' => (int) ($payload['user_id'] ?? 0)];
    }
}
