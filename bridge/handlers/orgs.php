<?php
/**
 * Organization command handlers.
 *
 * An organization's name is a column on ost_organization, but address / phone /
 * website / notes are answers of the organization dynamic form (type 'O'), so they are
 * written through FastApiModel::applyAnswers(). The API's `internal_notes` is the
 * form field named `notes`.
 *
 * Payload field names match app/modules/users/models.py (OrganizationCreate,
 * OrganizationUpdate).
 */

require_once INCLUDE_DIR . 'class.organization.php';
require_once dirname(__FILE__) . '/common.php';

class FastApiOrgHandler {

    /** API field => dynamic form field name */
    private static $FORM_FIELDS = [
        'address'        => 'address',
        'phone'          => 'phone',
        'website'        => 'website',
        'internal_notes' => 'notes',
    ];

    private static function requireOrg($org_id) {
        $org = Organization::lookup((int) $org_id);
        if (!$org) {
            throw new Exception("Organization not found: {$org_id}");
        }
        return $org;
    }

    private static function formValues(array $payload) {
        $values = [];
        foreach (self::$FORM_FIELDS as $api => $field) {
            // exclude_none on the Python side: an absent key means "leave as is"
            if (array_key_exists($api, $payload) && $payload[$api] !== null) {
                $values[$field] = $payload[$api];
            }
        }
        return $values;
    }

    private static function applyForm(Organization $org, array $payload) {
        $values = self::formValues($payload);
        if ($values && ($errors = FastApiModel::applyAnswers($org, $values, 'O'))) {
            throw new Exception('Organization data rejected: ' . json_encode($errors));
        }
    }

    private static function uniqueName($name, $except_id = 0) {
        $name = trim(Format::striptags((string) $name));
        if ($name === '') {
            throw new Exception("Organization 'name' is required.");
        }
        $other = Organization::lookup(['name' => $name]);
        if ($other && (int) $other->getId() !== (int) $except_id) {
            throw new Exception("An organization named '{$name}' already exists.");
        }
        return $name;
    }

    public static function create($payload, $staff_id) {
        $name = self::uniqueName($payload['name'] ?? '');

        // fromVars() creates the row (and fires osTicket's created signals); the form data
        // follows, and a rejected value rolls the new organization back.
        $org = Organization::fromVars(['name' => $name]);
        if (!$org || !$org->getId()) {
            throw new Exception('Organization was not created.');
        }
        try {
            self::applyForm($org, $payload);
        } catch (Exception $e) {
            $org->delete();   // don't leave a half-populated organization behind
            throw $e;
        }
        return ['org_id' => $org->getId(), 'name' => $org->name];
    }

    public static function update($payload, $staff_id) {
        $org = self::requireOrg($payload['org_id'] ?? 0);

        if (isset($payload['name'])) {
            $name = self::uniqueName($payload['name'], $org->getId());
        }
        self::applyForm($org, $payload);
        if (isset($name) && $name !== $org->name) {
            $org->name = $name;
            $org->updated = SqlFunction::NOW();
            if (!$org->save()) {
                throw new Exception('Organization name could not be saved.');
            }
        }
        return ['org_id' => $org->getId()];
    }

    public static function delete($payload, $staff_id) {
        $org = self::requireOrg($payload['org_id'] ?? 0);
        if (!$org->delete()) {
            throw new Exception('Organization could not be deleted.');
        }
        return ['org_id' => (int) ($payload['org_id'] ?? 0)];
    }
}
