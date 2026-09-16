-- ============================================================================
-- fast-ticket integration tables
-- Run automatically by MariaDB on first container start via docker-entrypoint-initdb.d
-- ============================================================================

-- Command inbox: FastAPI → PHP bridge
CREATE TABLE IF NOT EXISTS `ost_integration_inbox` (
    `id`                    INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `command_type`          VARCHAR(100)    NOT NULL COMMENT 'e.g. ticket.create, ticket.reply',
    `aggregate_type`        VARCHAR(50)     NOT NULL COMMENT 'e.g. ticket, task, user',
    `aggregate_id`          VARCHAR(50)     NOT NULL DEFAULT '' COMMENT 'osTicket PK as string',
    `payload`               MEDIUMTEXT      NOT NULL DEFAULT '{}',
    `idempotency_key`       VARCHAR(255)    NULL UNIQUE COMMENT 'Client-supplied dedup key',
    `status`                ENUM('pending','processing','done','failed')
                                            NOT NULL DEFAULT 'pending',
    `requested_by_staff_id` INT UNSIGNED    NULL,
    `result_payload`        MEDIUMTEXT      NULL COMMENT 'JSON response from PHP side',
    `error_message`         TEXT            NULL,
    `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `processed_at`          DATETIME        NULL,
    PRIMARY KEY (`id`),
    INDEX `idx_inbox_status` (`status`),
    INDEX `idx_inbox_command_type` (`command_type`),
    INDEX `idx_inbox_aggregate` (`aggregate_type`, `aggregate_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Event outbox: PHP bridge → Python worker
CREATE TABLE IF NOT EXISTS `ost_integration_outbox` (
    `id`                    INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `event_type`            VARCHAR(100)    NOT NULL COMMENT 'e.g. ticket.created',
    `aggregate_type`        VARCHAR(50)     NOT NULL,
    `aggregate_id`          VARCHAR(50)     NOT NULL DEFAULT '',
    `payload`               MEDIUMTEXT      NOT NULL DEFAULT '{}',
    `status`                ENUM('pending','processing','processed','failed')
                                            NOT NULL DEFAULT 'pending',
    `retry_count`           TINYINT         NOT NULL DEFAULT 0,
    `error_message`         TEXT            NULL,
    `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `locked_at`             DATETIME        NULL COMMENT 'Set when a worker claims this event',
    `processed_at`          DATETIME        NULL,
    PRIMARY KEY (`id`),
    INDEX `idx_outbox_status` (`status`),
    INDEX `idx_outbox_event_type` (`event_type`),
    INDEX `idx_outbox_aggregate` (`aggregate_type`, `aggregate_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
