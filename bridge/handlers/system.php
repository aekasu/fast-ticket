<?php
/**
 * System logs (scp/logs.php: mass delete, which osTicket does with one DELETE on the syslog table).
 */
if (!class_exists('FastApiLogHandler')) {

class FastApiLogHandler {

    public static function delete($payload, $staff_id) {
        $ids = array_values(array_unique(array_filter(array_map('intval', (array) ($payload['ids'] ?? [])))));
        if (!$ids) {
            throw new Exception("At least one log entry ('ids') is required.");
        }
        $sql = 'DELETE FROM ' . SYSLOG_TABLE . ' WHERE log_id IN (' . implode(',', $ids) . ')';
        if (!db_query($sql)) {
            throw new Exception('Log entries could not be deleted.');
        }
        return ['deleted' => (int) db_affected_rows(), 'requested' => count($ids)];
    }
}
}
