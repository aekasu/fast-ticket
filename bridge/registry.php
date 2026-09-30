<?php
/**
 * Command registry — the single place a command type is defined on the PHP side.
 *
 *   command type => [handlers/<file>.php, handler class, static method, outbox event]
 *
 * The dispatcher (plugin.php) calls Class::method($payload, $requested_by_staff_id) and
 * emits the event on success. A command type exists only when a Python service also
 * dispatches it (tests/unit/test_bridge_registry.py enforces both directions).
 * Keep one entry per line: that test parses this file as text.
 */

return [

    'ticket.create'           => ['tickets', 'FastApiTicketHandler', 'create', 'ticket.created'],
    'ticket.reply'            => ['tickets', 'FastApiTicketHandler', 'reply', 'ticket.replied'],
    'ticket.note'             => ['tickets', 'FastApiTicketHandler', 'addNote', 'ticket.note_added'],
    'ticket.close'            => ['tickets', 'FastApiTicketHandler', 'close', 'ticket.closed'],
    'ticket.reopen'           => ['tickets', 'FastApiTicketHandler', 'reopen', 'ticket.reopened'],
    'ticket.assign'           => ['tickets', 'FastApiTicketHandler', 'assign', 'ticket.assigned'],
    'ticket.assign_team'      => ['tickets', 'FastApiTicketHandler', 'assignTeam', 'ticket.team_assigned'],
    'ticket.transfer'         => ['tickets', 'FastApiTicketHandler', 'transfer', 'ticket.transferred'],
    'ticket.change_owner'     => ['tickets', 'FastApiTicketHandler', 'changeOwner', 'ticket.owner_changed'],
    'ticket.delete'           => ['tickets', 'FastApiTicketHandler', 'delete', 'ticket.deleted'],
    'ticket.merge'            => ['tickets', 'FastApiTicketHandler', 'merge', 'ticket.merged'],
    'ticket.answered'         => ['tickets', 'FastApiTicketHandler', 'answered', 'ticket.answered'],
    'ticket.unanswered'       => ['tickets', 'FastApiTicketHandler', 'unanswered', 'ticket.unanswered'],
    'ticket.collaborator.add' => ['tickets', 'FastApiTicketHandler', 'addCollaborator', 'ticket.collaborator_added'],

    'task.create'             => ['tasks', 'FastApiTaskHandler', 'create', 'task.created'],
    'task.assign'             => ['tasks', 'FastApiTaskHandler', 'assign', 'task.assigned'],
    'task.close'              => ['tasks', 'FastApiTaskHandler', 'close', 'task.closed'],
    'task.delete'             => ['tasks', 'FastApiTaskHandler', 'delete', 'task.deleted'],
    'task.reopen'             => ['tasks', 'FastApiTaskHandler', 'reopen', 'task.reopened'],
    'task.note'               => ['tasks', 'FastApiTaskHandler', 'addNote', 'task.note_added'],
    'task.edit'               => ['tasks', 'FastApiTaskHandler', 'edit', 'task.edited'],

    'user.create'             => ['users', 'FastApiUserHandler', 'create', 'user.created'],
    'user.update'             => ['users', 'FastApiUserHandler', 'update', 'user.updated'],
    'user.delete'             => ['users', 'FastApiUserHandler', 'delete', 'user.deleted'],

    'org.create'              => ['orgs', 'FastApiOrgHandler', 'create', 'org.created'],
    'org.update'              => ['orgs', 'FastApiOrgHandler', 'update', 'org.updated'],
    'org.delete'              => ['orgs', 'FastApiOrgHandler', 'delete', 'org.deleted'],

    'kb.category.create'      => ['kb', 'FastApiKbHandler', 'createCategory', 'kb.category.created'],
    'kb.category.update'      => ['kb', 'FastApiKbHandler', 'updateCategory', 'kb.category.updated'],
    'kb.category.delete'      => ['kb', 'FastApiKbHandler', 'deleteCategory', 'kb.category.deleted'],
    'kb.faq.create'           => ['kb', 'FastApiKbHandler', 'createFaq', 'kb.faq.created'],
    'kb.faq.update'           => ['kb', 'FastApiKbHandler', 'updateFaq', 'kb.faq.updated'],
    'kb.faq.delete'           => ['kb', 'FastApiKbHandler', 'deleteFaq', 'kb.faq.deleted'],
    'kb.canned.create'        => ['kb', 'FastApiKbHandler', 'createCanned', 'kb.canned.created'],
    'kb.canned.update'        => ['kb', 'FastApiKbHandler', 'updateCanned', 'kb.canned.updated'],
    'kb.canned.delete'        => ['kb', 'FastApiKbHandler', 'deleteCanned', 'kb.canned.deleted'],

    'staff.create'            => ['staff', 'FastApiStaffHandler', 'create', 'staff.created'],
    'staff.update'            => ['staff', 'FastApiStaffHandler', 'update', 'staff.updated'],
    'staff.delete'            => ['staff', 'FastApiStaffHandler', 'delete', 'staff.deleted'],

    'dept.create'             => ['departments', 'FastApiDepartmentHandler', 'create', 'dept.created'],
    'dept.update'             => ['departments', 'FastApiDepartmentHandler', 'update', 'dept.updated'],
    'dept.delete'             => ['departments', 'FastApiDepartmentHandler', 'delete', 'dept.deleted'],

    'team.create'             => ['teams', 'FastApiTeamHandler', 'create', 'team.created'],
    'team.update'             => ['teams', 'FastApiTeamHandler', 'update', 'team.updated'],
    'team.delete'             => ['teams', 'FastApiTeamHandler', 'delete', 'team.deleted'],

    'topic.create'            => ['lookups', 'FastApiTopicHandler', 'create', 'topic.created'],
    'topic.update'            => ['lookups', 'FastApiTopicHandler', 'update', 'topic.updated'],
    'topic.delete'            => ['lookups', 'FastApiTopicHandler', 'delete', 'topic.deleted'],

    'sla.create'              => ['lookups', 'FastApiSlaHandler', 'create', 'sla.created'],
    'sla.update'              => ['lookups', 'FastApiSlaHandler', 'update', 'sla.updated'],
    'sla.delete'              => ['lookups', 'FastApiSlaHandler', 'delete', 'sla.deleted'],

    'priority.create'         => ['lookups', 'FastApiPriorityHandler', 'create', 'priority.created'],
    'priority.update'         => ['lookups', 'FastApiPriorityHandler', 'update', 'priority.updated'],
    'priority.delete'         => ['lookups', 'FastApiPriorityHandler', 'delete', 'priority.deleted'],

    'form.create'             => ['forms', 'FastApiFormHandler', 'create', 'form.created'],
    'form.update'             => ['forms', 'FastApiFormHandler', 'update', 'form.updated'],
    'form.delete'             => ['forms', 'FastApiFormHandler', 'delete', 'form.deleted'],

    'banlist.create'          => ['banlist', 'FastApiBanlistHandler', 'create', 'banlist.created'],
    'banlist.update'          => ['banlist', 'FastApiBanlistHandler', 'update', 'banlist.updated'],
    'banlist.delete'          => ['banlist', 'FastApiBanlistHandler', 'delete', 'banlist.deleted'],
];
