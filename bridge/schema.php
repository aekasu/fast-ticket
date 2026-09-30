<?php
/**
 * The plugin's two tables — the only tables it owns. `{inbox}` / `{outbox}` are replaced with
 * TABLE_PREFIX . 'integration_inbox' / 'integration_outbox' (FastApiIntegrationPlugin::ensureTables()).
 *
 * Written to run on MySQL 5.6+ / MariaDB 10+: no DEFAULT on TEXT columns (MySQL < 8.0.13
 * refuses it; every writer supplies a payload). tests/unit/test_bridge_schema.py keeps the
 * column names in step with the SQLModel classes in app/integration/osticket/.
 */

return [
    // Command inbox: FastAPI → PHP bridge
    "CREATE TABLE IF NOT EXISTS `{inbox}` (
        `id`                    INT UNSIGNED    NOT NULL AUTO_INCREMENT,
        `command_type`          VARCHAR(100)    NOT NULL,
        `aggregate_type`        VARCHAR(50)     NOT NULL,
        `aggregate_id`          VARCHAR(50)     NOT NULL DEFAULT '',
        `payload`               MEDIUMTEXT      NOT NULL,
        `idempotency_key`       VARCHAR(255)    NULL,
        `status`                ENUM('pending','processing','done','failed') NOT NULL DEFAULT 'pending',
        `requested_by_staff_id` INT UNSIGNED    NULL,
        `claimed_by`            VARCHAR(32)     NULL,
        `result_payload`        MEDIUMTEXT      NULL,
        `error_message`         TEXT            NULL,
        `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `processed_at`          DATETIME        NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `idempotency_key` (`idempotency_key`),
        KEY `idx_inbox_status` (`status`, `id`),
        KEY `idx_inbox_claimed_by` (`claimed_by`),
        KEY `idx_inbox_command_type` (`command_type`),
        KEY `idx_inbox_aggregate` (`aggregate_type`, `aggregate_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",

    // Event outbox: PHP bridge → Python worker
    "CREATE TABLE IF NOT EXISTS `{outbox}` (
        `id`                    INT UNSIGNED    NOT NULL AUTO_INCREMENT,
        `event_type`            VARCHAR(100)    NOT NULL,
        `aggregate_type`        VARCHAR(50)     NOT NULL,
        `aggregate_id`          VARCHAR(50)     NOT NULL DEFAULT '',
        `payload`               MEDIUMTEXT      NOT NULL,
        `status`                ENUM('pending','processing','processed','failed') NOT NULL DEFAULT 'pending',
        `retry_count`           TINYINT         NOT NULL DEFAULT 0,
        `error_message`         TEXT            NULL,
        `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `locked_at`             DATETIME        NULL,
        `processed_at`          DATETIME        NULL,
        PRIMARY KEY (`id`),
        KEY `idx_outbox_status` (`status`, `created_at`),
        KEY `idx_outbox_event_type` (`event_type`),
        KEY `idx_outbox_aggregate` (`aggregate_type`, `aggregate_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
];
