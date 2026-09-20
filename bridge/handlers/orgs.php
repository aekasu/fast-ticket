<?php

require_once INCLUDE_DIR . 'class.organization.php';

class FastApiOrgHandler {
    
    public static function create($payload, $staff_id) {
        if (!isset($payload['name']) || trim($payload['name']) === '') {
            throw new Exception("Organization 'name' is required.");
        }

        $vars = array(
            'name' => trim($payload['name']),
            'manager' => isset($payload['manager_id']) ? $payload['manager_id'] : null,
            'notes' => isset($payload['notes']) ? $payload['notes'] : ''
        );

        $errors = array();
        $org = Organization::fromVars($vars, $errors);
        
        if (!$org || !empty($errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error creating organization.';
            throw new Exception("Failed to create organization: " . $error_msg);
        }

        return array('org_id' => $org->getId());
    }

    public static function update($payload, $staff_id) {
        if (!isset($payload['org_id'])) {
            throw new Exception("Organization ID ('org_id') is required for update.");
        }

        $org = Organization::lookup($payload['org_id']);
        if (!$org) {
            throw new Exception("Organization not found.");
        }

        $vars = $payload;
        $errors = array();
        
        if (!$org->update($vars, $errors)) {
            $error_msg = !empty($errors) ? json_encode($errors) : 'Unknown error updating organization.';
            throw new Exception("Failed to update organization: " . $error_msg);
        }

        return array('org_id' => $org->getId());
    }

    public static function delete($payload, $staff_id) {
        if (!isset($payload['org_id'])) {
            throw new Exception("Organization ID ('org_id') is required for deletion.");
        }

        $org = Organization::lookup($payload['org_id']);
        if (!$org) {
            throw new Exception("Organization not found.");
        }

        if (!$org->delete()) {
            throw new Exception("Failed to delete organization.");
        }

        return array('success' => true);
    }
}
