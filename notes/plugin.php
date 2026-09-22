<?php
set_include_path(get_include_path().PATH_SEPARATOR.dirname(__file__).'/include');
return array(
    'id' =>             'fastapi:integration', # notrans
    'version' =>        '0.1',
    'name' =>           /* trans */ 'FastAPI Integration Bridge',
    'author' =>         'Antigravity',
    'description' =>    /* trans */ 'Bridges osTicket events to a FastAPI application via an outbox pattern.',
    'url' =>            'https://github.com/osTicket/osTicket',
    'plugin' =>         'integration.php:FastApiIntegrationPlugin',
);
