<?php
/**
 * Command registry — the single place a command type is defined on the PHP side.
 *
 *   command type => [handlers/<file>.php, handler class, static method, outbox event, permission]
 *
 * The dispatcher (plugin.php) first checks that the requesting staff member holds `permission`,
 * then calls Class::method($payload, $requested_by_staff_id) and emits the event on success.
 * `permission` is what osTicket's own page for the job requires, so the bridge enforces it even if
 * the API's check were bypassed or wrong:
 *
 *   'admin'            $thisstaff->isAdmin()                     (Admin Panel pages)
 *   'global:<key>'     $thisstaff->hasPerm('<key>')              (user.*, org.*, faq.manage, ...)
 *   'role:<key>'       $thisstaff->hasPerm('<key>', false)       (any of the agent's roles: ticket.*, task.*, canned.manage)
 *
 * There is no admin bypass — osTicket's hasPerm() has none. Ticket and task handlers additionally apply
 * osTicket's exact per-ticket / per-task check (checkStaffPerm). tests/unit/test_bridge_registry.py checks
 * every entry against the guard the API's own route declares. A command type exists only when a Python service also
 * dispatches it (tests/unit/test_bridge_registry.py enforces both directions).
 * Keep one entry per line: that test parses this file as text.
 */

return [

    'ticket.create'           => ['tickets', 'FastApiTicketHandler', 'create', 'ticket.created', 'role:ticket.create'],
    'ticket.reply'            => ['tickets', 'FastApiTicketHandler', 'reply', 'ticket.replied', 'role:ticket.reply'],
    'ticket.note'             => ['tickets', 'FastApiTicketHandler', 'addNote', 'ticket.note_added', 'role:ticket.reply'],
    'ticket.close'            => ['tickets', 'FastApiTicketHandler', 'close', 'ticket.closed', 'role:ticket.close'],
    'ticket.reopen'           => ['tickets', 'FastApiTicketHandler', 'reopen', 'ticket.reopened', 'role:ticket.close'],
    'ticket.assign'           => ['tickets', 'FastApiTicketHandler', 'assign', 'ticket.assigned', 'role:ticket.assign'],
    'ticket.assign_team'      => ['tickets', 'FastApiTicketHandler', 'assignTeam', 'ticket.team_assigned', 'role:ticket.assign'],
    'ticket.claim'            => ['tickets', 'FastApiTicketHandler', 'claim', 'ticket.claimed', 'role:ticket.assign'],
    'ticket.release'          => ['tickets', 'FastApiTicketHandler', 'release', 'ticket.released', 'role:ticket.release'],
    'ticket.refer'            => ['tickets', 'FastApiTicketHandler', 'refer', 'ticket.referred', 'role:ticket.assign'],
    'ticket.transfer'         => ['tickets', 'FastApiTicketHandler', 'transfer', 'ticket.transferred', 'role:ticket.transfer'],
    'ticket.update'           => ['tickets', 'FastApiTicketHandler', 'update', 'ticket.updated', 'role:ticket.edit'],
    'ticket.change_owner'     => ['tickets', 'FastApiTicketHandler', 'changeOwner', 'ticket.owner_changed', 'role:ticket.edit'],
    'ticket.delete'           => ['tickets', 'FastApiTicketHandler', 'delete', 'ticket.deleted', 'role:ticket.delete'],
    'ticket.merge'            => ['tickets', 'FastApiTicketHandler', 'merge', 'ticket.merged', 'role:ticket.merge'],
    'ticket.answered'         => ['tickets', 'FastApiTicketHandler', 'answered', 'ticket.answered', 'role:ticket.markanswered'],
    'ticket.unanswered'       => ['tickets', 'FastApiTicketHandler', 'unanswered', 'ticket.unanswered', 'role:ticket.markanswered'],
    'ticket.referral.remove'  => ['tickets', 'FastApiTicketHandler', 'removeReferral', 'ticket.referral_removed', 'role:ticket.assign'],
    'ticket.collaborator.add' => ['tickets', 'FastApiTicketHandler', 'addCollaborator', 'ticket.collaborator_added', 'role:ticket.edit'],
    'ticket.collaborator.update' => ['tickets', 'FastApiTicketHandler', 'updateCollaborator', 'ticket.collaborator_updated', 'role:ticket.edit'],
    'ticket.collaborator.remove' => ['tickets', 'FastApiTicketHandler', 'removeCollaborator', 'ticket.collaborator_removed', 'role:ticket.edit'],
    'ticket.entry.edit'       => ['tickets', 'FastApiTicketHandler', 'editEntry', 'ticket.entry_edited', 'role:ticket.reply'],
    'ticket.entry.resend'     => ['tickets', 'FastApiTicketHandler', 'resendEntry', 'ticket.entry_resent', 'role:ticket.reply'],

    'task.create'             => ['tasks', 'FastApiTaskHandler', 'create', 'task.created', 'role:task.create'],
    'task.assign'             => ['tasks', 'FastApiTaskHandler', 'assign', 'task.assigned', 'role:task.assign'],
    'task.claim'              => ['tasks', 'FastApiTaskHandler', 'claim', 'task.claimed', 'role:task.assign'],
    'task.transfer'           => ['tasks', 'FastApiTaskHandler', 'transfer', 'task.transferred', 'role:task.transfer'],
    'task.reply'              => ['tasks', 'FastApiTaskHandler', 'reply', 'task.replied', 'role:task.reply'],
    'task.close'              => ['tasks', 'FastApiTaskHandler', 'close', 'task.closed', 'role:task.close'],
    'task.delete'             => ['tasks', 'FastApiTaskHandler', 'delete', 'task.deleted', 'role:task.delete'],
    'task.reopen'             => ['tasks', 'FastApiTaskHandler', 'reopen', 'task.reopened', 'role:task.close'],
    'task.note'               => ['tasks', 'FastApiTaskHandler', 'addNote', 'task.note_added', 'role:task.reply'],
    'task.edit'               => ['tasks', 'FastApiTaskHandler', 'edit', 'task.edited', 'role:task.edit'],

    'user.create'             => ['users', 'FastApiUserHandler', 'create', 'user.created', 'global:user.create'],
    'user.update'             => ['users', 'FastApiUserHandler', 'update', 'user.updated', 'global:user.edit'],
    'user.delete'             => ['users', 'FastApiUserHandler', 'delete', 'user.deleted', 'global:user.delete'],
    'user.account.register'   => ['users', 'FastApiUserHandler', 'registerAccount', 'user.account_registered', 'global:user.manage'],
    'user.account.update'     => ['users', 'FastApiUserHandler', 'updateAccount', 'user.account_updated', 'global:user.manage'],
    'user.account.password_reset' => ['users', 'FastApiUserHandler', 'sendPasswordReset', 'user.password_reset_sent', 'global:user.manage'],
    'user.account.confirmation' => ['users', 'FastApiUserHandler', 'sendConfirmation', 'user.confirmation_sent', 'global:user.manage'],

    'org.create'              => ['orgs', 'FastApiOrgHandler', 'create', 'org.created', 'global:org.create'],
    'org.update'              => ['orgs', 'FastApiOrgHandler', 'update', 'org.updated', 'global:org.edit'],
    'org.delete'              => ['orgs', 'FastApiOrgHandler', 'delete', 'org.deleted', 'global:org.delete'],

    'kb.category.create'      => ['kb', 'FastApiKbHandler', 'createCategory', 'kb.category.created', 'global:faq.manage'],
    'kb.category.update'      => ['kb', 'FastApiKbHandler', 'updateCategory', 'kb.category.updated', 'global:faq.manage'],
    'kb.category.delete'      => ['kb', 'FastApiKbHandler', 'deleteCategory', 'kb.category.deleted', 'global:faq.manage'],
    'kb.faq.create'           => ['kb', 'FastApiKbHandler', 'createFaq', 'kb.faq.created', 'global:faq.manage'],
    'kb.faq.update'           => ['kb', 'FastApiKbHandler', 'updateFaq', 'kb.faq.updated', 'global:faq.manage'],
    'kb.faq.delete'           => ['kb', 'FastApiKbHandler', 'deleteFaq', 'kb.faq.deleted', 'global:faq.manage'],
    'kb.canned.create'        => ['kb', 'FastApiKbHandler', 'createCanned', 'kb.canned.created', 'role:canned.manage'],
    'kb.canned.update'        => ['kb', 'FastApiKbHandler', 'updateCanned', 'kb.canned.updated', 'role:canned.manage'],
    'kb.canned.delete'        => ['kb', 'FastApiKbHandler', 'deleteCanned', 'kb.canned.deleted', 'role:canned.manage'],

    'staff.create'            => ['staff', 'FastApiStaffHandler', 'create', 'staff.created', 'admin'],
    'staff.update'            => ['staff', 'FastApiStaffHandler', 'update', 'staff.updated', 'admin'],
    'staff.delete'            => ['staff', 'FastApiStaffHandler', 'delete', 'staff.deleted', 'admin'],

    'dept.create'             => ['departments', 'FastApiDepartmentHandler', 'create', 'dept.created', 'admin'],
    'dept.update'             => ['departments', 'FastApiDepartmentHandler', 'update', 'dept.updated', 'admin'],
    'dept.delete'             => ['departments', 'FastApiDepartmentHandler', 'delete', 'dept.deleted', 'admin'],

    'team.create'             => ['teams', 'FastApiTeamHandler', 'create', 'team.created', 'admin'],
    'team.update'             => ['teams', 'FastApiTeamHandler', 'update', 'team.updated', 'admin'],
    'team.delete'             => ['teams', 'FastApiTeamHandler', 'delete', 'team.deleted', 'admin'],

    'topic.create'            => ['lookups', 'FastApiTopicHandler', 'create', 'topic.created', 'admin'],
    'topic.update'            => ['lookups', 'FastApiTopicHandler', 'update', 'topic.updated', 'admin'],
    'topic.delete'            => ['lookups', 'FastApiTopicHandler', 'delete', 'topic.deleted', 'admin'],

    'sla.create'              => ['lookups', 'FastApiSlaHandler', 'create', 'sla.created', 'admin'],
    'sla.update'              => ['lookups', 'FastApiSlaHandler', 'update', 'sla.updated', 'admin'],
    'sla.delete'              => ['lookups', 'FastApiSlaHandler', 'delete', 'sla.deleted', 'admin'],

    'priority.create'         => ['lookups', 'FastApiPriorityHandler', 'create', 'priority.created', 'admin'],
    'priority.update'         => ['lookups', 'FastApiPriorityHandler', 'update', 'priority.updated', 'admin'],
    'priority.delete'         => ['lookups', 'FastApiPriorityHandler', 'delete', 'priority.deleted', 'admin'],

    'form.create'             => ['forms', 'FastApiFormHandler', 'create', 'form.created', 'admin'],
    'form.update'             => ['forms', 'FastApiFormHandler', 'update', 'form.updated', 'admin'],
    'form.delete'             => ['forms', 'FastApiFormHandler', 'delete', 'form.deleted', 'admin'],
    'form.field.add'          => ['forms', 'FastApiFormHandler', 'addField', 'form.field_added', 'admin'],
    'form.field.update'       => ['forms', 'FastApiFormHandler', 'updateField', 'form.field_updated', 'admin'],
    'form.field.delete'       => ['forms', 'FastApiFormHandler', 'deleteField', 'form.field_deleted', 'admin'],

    'banlist.create'          => ['banlist', 'FastApiBanlistHandler', 'create', 'banlist.created', 'admin'],
    'banlist.update'          => ['banlist', 'FastApiBanlistHandler', 'update', 'banlist.updated', 'admin'],
    'banlist.delete'          => ['banlist', 'FastApiBanlistHandler', 'delete', 'banlist.deleted', 'admin'],
];
