<?php
/**
 * The Admin Panel settings pages (scp/settings.php) through Config::updateSettings(), which validates each section
 * as the page does and rewrites every key of it. An update therefore starts from the stored values and overlays the
 * payload: a checkbox is "posted" (present) when on, a flag is posted as 1 or 0.
 *
 * SECTIONS is mirrored from app/modules/settings/sections.py; tests/unit/test_settings_sections.py compares them.
 */
if (!class_exists('FastApiSettingsHandler')) {

class FastApiSettingsHandler {

    const SECTIONS = [
        'system' => [
            'isonline' => 'flag', 'helpdesk_title' => 'str', 'helpdesk_url' => 'str', 'default_dept_id' => 'int',
            'force_https' => 'check', 'max_page_size' => 'int', 'log_level' => 'int', 'log_graceperiod' => 'int',
            'time_format' => 'str', 'date_format' => 'str', 'datetime_format' => 'str',
            'daydatetime_format' => 'str', 'default_timezone' => 'str', 'schedule_id' => 'int',
            'default_locale' => 'str', 'system_language' => 'str', 'secondary_langs' => 'list',
            'max_file_size' => 'int', 'autolock_minutes' => 'int', 'enable_avatars' => 'check',
            'enable_richtext' => 'check', 'files_req_auth' => 'check', 'allow_iframes' => 'str',
            'embedded_domain_whitelist' => 'str', 'acl' => 'str', 'acl_backend' => 'int',
        ],
        'tickets' => [
            'ticket_number_format' => 'str', 'ticket_sequence_id' => 'int', 'queue_bucket_counts' => 'check',
            'default_priority_id' => 'int', 'default_help_topic' => 'int', 'default_ticket_status_id' => 'int',
            'default_sla_id' => 'int', 'max_open_tickets' => 'int', 'enable_captcha' => 'check',
            'auto_claim_tickets' => 'check', 'auto_refer_closed' => 'check',
            'collaborator_ticket_visibility' => 'check', 'require_topic_to_close' => 'check',
            'show_related_tickets' => 'check', 'allow_client_updates' => 'check', 'ticket_lock' => 'int',
            'default_ticket_queue' => 'int', 'allow_external_images' => 'check', 'ticket_autoresponder' => 'check',
            'message_autoresponder' => 'check', 'message_autoresponder_collabs' => 'check',
            'ticket_notice_active' => 'check', 'overlimit_notice_active' => 'check', 'ticket_alert_active' => 'flag',
            'ticket_alert_admin' => 'check', 'ticket_alert_dept_manager' => 'check',
            'ticket_alert_dept_members' => 'check', 'ticket_alert_acct_manager' => 'check',
            'message_alert_active' => 'flag', 'message_alert_laststaff' => 'check',
            'message_alert_assigned' => 'check', 'message_alert_dept_manager' => 'check',
            'message_alert_acct_manager' => 'check', 'note_alert_active' => 'flag',
            'note_alert_laststaff' => 'check', 'note_alert_assigned' => 'check',
            'note_alert_dept_manager' => 'check', 'assigned_alert_active' => 'flag',
            'assigned_alert_staff' => 'check', 'assigned_alert_team_lead' => 'check',
            'assigned_alert_team_members' => 'check', 'transfer_alert_active' => 'flag',
            'transfer_alert_assigned' => 'check', 'transfer_alert_dept_manager' => 'check',
            'transfer_alert_dept_members' => 'check', 'overdue_alert_active' => 'flag',
            'overdue_alert_assigned' => 'check', 'overdue_alert_dept_manager' => 'check',
            'overdue_alert_dept_members' => 'check', 'send_sys_errors' => 'check', 'send_sql_errors' => 'check',
            'send_login_errors' => 'check',
        ],
        'tasks' => [
            'task_number_format' => 'str', 'task_sequence_id' => 'int', 'default_task_priority_id' => 'int',
            'default_task_sla_id' => 'int', 'task_alert_active' => 'flag', 'task_alert_admin' => 'check',
            'task_alert_dept_manager' => 'check', 'task_alert_dept_members' => 'check',
            'task_activity_alert_active' => 'flag', 'task_activity_alert_laststaff' => 'check',
            'task_activity_alert_assigned' => 'check', 'task_activity_alert_dept_manager' => 'check',
            'task_assignment_alert_active' => 'flag', 'task_assignment_alert_staff' => 'check',
            'task_assignment_alert_team_lead' => 'check', 'task_assignment_alert_team_members' => 'check',
            'task_transfer_alert_active' => 'flag', 'task_transfer_alert_assigned' => 'check',
            'task_transfer_alert_dept_manager' => 'check', 'task_transfer_alert_dept_members' => 'check',
            'task_overdue_alert_active' => 'flag', 'task_overdue_alert_assigned' => 'check',
            'task_overdue_alert_dept_manager' => 'check', 'task_overdue_alert_dept_members' => 'check',
        ],
        'agents' => [
            'agent_passwd_policy' => 'str', 'staff_max_logins' => 'int', 'staff_login_timeout' => 'int',
            'staff_session_timeout' => 'int', 'staff_ip_binding' => 'check', 'allow_pw_reset' => 'check',
            'pw_reset_window' => 'int', 'require_agent_2fa' => 'check', 'agent_name_format' => 'str',
            'hide_staff_name' => 'check', 'agent_avatar' => 'str', 'disable_agent_collabs' => 'check',
        ],
        'users' => [
            'client_passwd_policy' => 'str', 'client_max_logins' => 'int', 'client_login_timeout' => 'int',
            'client_session_timeout' => 'int', 'clients_only' => 'check', 'client_registration' => 'str',
            'client_verify_email' => 'check', 'allow_auth_tokens' => 'check', 'client_name_format' => 'str',
            'client_avatar' => 'str',
        ],
        'kb' => [
            'enable_kb' => 'check', 'restrict_kb' => 'check', 'enable_premade' => 'check',
        ],
        'emails' => [
            'default_template_id' => 'int', 'default_email_id' => 'int', 'alert_email_id' => 'int',
            'default_smtp_id' => 'int', 'admin_email' => 'str', 'verify_email_addrs' => 'check',
            'enable_auto_cron' => 'check', 'enable_mail_polling' => 'check', 'strip_quoted_reply' => 'check',
            'use_email_priority' => 'check', 'accept_unregistered_email' => 'check', 'add_email_collabs' => 'check',
            'reply_separator' => 'str', 'email_attachments' => 'check',
        ],
        'pages' => [
            'landing_page_id' => 'int', 'offline_page_id' => 'int', 'thank-you_page_id' => 'int',
        ],
    ];

    private static function truthy($value) {
        return in_array(strtolower(trim((string) $value)), ['1', 'on', 'true', 'yes'], true);
    }

    private static function firstOpenStatusId() {
        foreach (TicketStatusList::getStatuses(['enabled' => true, 'states' => ['open']]) as $status) {
            return $status->getId();
        }
        return 0;
    }

    public static function update($payload, $staff_id) {
        global $cfg;
        $section = (string) ($payload['section'] ?? '');
        if (!isset(self::SECTIONS[$section])) {
            throw new Exception("Unknown settings section: {$section}");
        }
        $values = (array) ($payload['values'] ?? []);
        $spec = self::SECTIONS[$section];
        foreach ($values as $key => $value) {
            if (!isset($spec[$key])) {
                throw new Exception("'{$key}' is not a {$section} setting.");
            }
        }

        // Stored state first (the page rewrites every key it knows), then the payload on top.
        $stored = $cfg->getConfigInfo();
        $vars = ['t' => $section];
        foreach ($spec as $key => $kind) {
            $value = array_key_exists($key, $values) ? $values[$key] : ($stored[$key] ?? null);
            switch ($kind) {
                case 'check':
                    if (self::truthy($value)) {
                        $vars[$key] = $key === 'force_https' ? 'on' : 1;
                    }
                    break;
                case 'flag':
                    $vars[$key] = self::truthy($value) ? 1 : 0;
                    break;
                case 'list':
                    $vars[$key] = is_array($value) ? array_values($value)
                        : array_values(array_filter(array_map('trim', explode(',', (string) $value))));
                    break;
                case 'int':
                    if ($value === null || $value === '') {
                        // osTicket's page shows a blank for a setting it never stored, and refuses to save it blank when
                        // the setting is required; 0 means "none / unlimited" and the default status is the first open one
                        $value = ($key === 'default_ticket_status_id') ? self::firstOpenStatusId() : 0;
                    }
                    $vars[$key] = (int) $value;
                    break;
                default:
                    $vars[$key] = $value === null ? '' : (string) $value;
            }
        }
        if ($section === 'system') {
            $vars['date_formats'] = $stored['date_formats'] ?? '';
            // an unchanged ACL list must not be refused as a lock-out when the admin's own address is not on it
            if (!array_key_exists('acl', $values) && !array_key_exists('acl_backend', $values)) {
                $vars['acl_backend'] = $vars['acl_backend'] ?: 0;
            }
        }

        $errors = [];
        if (!$cfg->updateSettings($vars, $errors) || $errors) {
            throw new Exception('Settings were not updated: ' . json_encode($errors ?: ['err' => 'unknown error']));
        }
        return ['section' => $section, 'keys' => array_keys($values)];
    }
}
}
