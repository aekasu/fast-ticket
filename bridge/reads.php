<?php
/**
 * Read registry: operation => [handler file in handlers/, class, method].
 *
 * A *read* asks osTicket's own PHP a question the API cannot answer from SQL faithfully (a canned response with its
 * variables filled in, the bytes of a file kept by any storage backend). It is served synchronously over HTTP by
 * `read.php` (POST /api/fast-ticket/read, the same integration key as login), as the staff member named in the
 * request, and never writes. The API decides who may ask (it checks the agent's access first); the handler checks
 * again with osTicket's own rules, so a leaked key reads no more than a staff member could.
 *
 * One entry per line: tests/unit/test_bridge_reads.py parses this file as text and checks it against the API's
 * `bridge_read` call sites.
 */
return [
    'canned.render' => ['reads', 'FastApiReadHandler', 'cannedRender'],
    'file.stream'   => ['reads', 'FastApiReadHandler', 'fileStream'],
    'dashboard.report' => ['reads', 'FastApiReadHandler', 'dashboardReport'],
    'system.info'   => ['reads', 'FastApiReadHandler', 'systemInfo'],
];
