<?php
/**
 * Schedules and their entries (scp/schedules.php, ajax.schedule.php). A schedule is written through Schedule::create() /
 * update() as the page does. An entry's form is parsed by osTicket in the viewer's locale and date format, so the entry is
 * built here from typed values the way ScheduleEntryForm::process() builds it, then checked with Schedule::isEntryUnique()
 * and stored with ScheduleEntry::create()/set(). A schedule an SLA, a department or the settings use cannot be deleted
 * (osTicket's own TODO; the page lets it through).
 */
include_once INCLUDE_DIR . 'class.schedule.php';

if (!class_exists('FastApiScheduleHandler')) {

class FastApiScheduleHandler {

    private static function requireSchedule($schedule_id) {
        $schedule = Schedule::lookup((int) $schedule_id);
        if (!$schedule) {
            throw new Exception("Schedule not found: {$schedule_id}");
        }
        return $schedule;
    }

    private static function errorText(array $errors) {
        return implode('; ', array_map('strval', $errors));
    }

    private static function checkHolidays(array $ids) {
        $ids = array_values(array_unique(array_map('intval', $ids)));
        foreach ($ids as $id) {
            $holiday = Schedule::lookup($id);
            if (!$holiday || $holiday->isBusinessHours()) {
                throw new Exception("Holiday schedule not found: {$id}");
            }
        }
        return $ids;
    }

    public static function create($payload, $staff_id) {
        $name = trim((string) ($payload['name'] ?? ''));
        if ($name === '') {
            throw new Exception("A schedule 'name' is required.");
        }
        if (Schedule::getIdByName($name)) {
            throw new Exception('Name already in use');
        }
        $bizhrs = ($payload['type'] ?? '') === 'business_hours';
        $source = null;
        if (!empty($payload['copy_from_id'])) {
            $source = self::requireSchedule($payload['copy_from_id']);
            if ((bool) $source->isBusinessHours() !== $bizhrs) {
                throw new Exception('Type must be of ' . $source->getTypeDesc() . ' to copy this schedule');
            }
        }
        $holidays = $bizhrs ? self::checkHolidays((array) ($payload['holiday_schedule_ids'] ?? [])) : [];
        $schedule = Schedule::create([
            'name' => $name, 'timezone' => (string) ($payload['timezone'] ?? ''), 'description' => (string) ($payload['description'] ?? ''),
        ]);
        if (!$schedule || !$schedule->getId()) {
            throw new Exception('Schedule was not stored.');
        }
        $schedule->setFlag(Schedule::FLAG_BIZHRS, $bizhrs);
        $schedule->save();
        if ($source) {
            $schedule->cloneEntries($source);
        }
        $schedule->saveConfiguration(['holidays' => $holidays]);
        return ['schedule_id' => $schedule->getId(), 'name' => $name];
    }

    public static function update($payload, $staff_id) {
        $schedule = self::requireSchedule($payload['schedule_id'] ?? 0);
        $name = array_key_exists('name', $payload) ? trim((string) $payload['name']) : $schedule->getName();
        if (($other = Schedule::getIdByName($name)) && (int) $other !== (int) $schedule->getId()) {
            throw new Exception('Name already in use');
        }
        $holidays = array_key_exists('holiday_schedule_ids', $payload)
            ? ($schedule->isBusinessHours() ? self::checkHolidays((array) $payload['holiday_schedule_ids']) : [])
            : (array) ($schedule->getHolidays() ?: []);
        $vars = [
            'name'        => $name,
            'type'        => $schedule->isBusinessHours() ? 'bizhrs' : 'hdays',
            'timezone'    => array_key_exists('timezone', $payload) ? (string) $payload['timezone'] : (string) $schedule->timezone,
            'description' => array_key_exists('description', $payload) ? (string) $payload['description'] : (string) $schedule->description,
            'holidays'    => $holidays,
        ];
        if (!$schedule->update($vars, $errors) || !empty($errors)) {
            throw new Exception('Schedule was not updated: ' . self::errorText($errors ?: ['the form was not valid']));
        }
        return ['schedule_id' => $schedule->getId()];
    }

    public static function delete($payload, $staff_id) {
        global $cfg;
        $schedule = self::requireSchedule($payload['schedule_id'] ?? 0);
        $id = (int) $schedule->getId();
        $uses = (int) db_result(db_query('SELECT COUNT(*) FROM ' . SLA_TABLE . ' WHERE schedule_id=' . $id))
              + (int) db_result(db_query('SELECT COUNT(*) FROM ' . DEPT_TABLE . ' WHERE schedule_id=' . $id))
              + ((int) $cfg->get('schedule_id') === $id ? 1 : 0);
        foreach (Schedule::objects()->filter(['flags__hasbit' => Schedule::FLAG_BIZHRS]) as $biz) {
            if (in_array($id, array_map('intval', (array) ($biz->getHolidays() ?: [])), true)) {
                $uses++;
            }
        }
        if ($uses) {
            throw new Exception('This schedule is in use (an SLA plan, a department, the system settings or a business-hours schedule).');
        }
        if (!$schedule->delete()) {
            throw new Exception('Schedule could not be deleted.');
        }
        return ['schedule_id' => $id];
    }

    /** The values osTicket stores for an entry, built as ScheduleEntryForm::process() builds them. */
    private static function entryVars($payload) {
        $repeats = (string) ($payload['repeats'] ?? '');
        $name = trim(Format::striptags((string) ($payload['name'] ?? '')));
        if ($name === '') {
            throw new Exception('Name is required');
        }
        $startsOn = DateTime::createFromFormat('!Y-m-d', (string) ($payload['starts_on'] ?? ''));
        if (!$startsOn) {
            throw new Exception('Valid Start Date required');
        }
        $allDay = !empty($payload['all_day']);
        $at = function ($v) {
            $v = (string) $v;
            return strlen($v) === 5 ? $v . ':00' : $v;
        };
        $startsAt = $allDay ? '00:00:00' : $at($payload['starts_at'] ?? '');
        $endsAt = $allDay ? '23:59:59' : $at($payload['ends_at'] ?? '');
        $day = $startsOn->format('Y-m-d');
        if (strtotime("$day $startsAt") >= strtotime("$day $endsAt") + 59) {
            throw new Exception('Invalid time span');
        }
        $vars = ['name' => $name, 'repeats' => $repeats, 'starts_on' => $day, 'starts_at' => substr($startsAt, 0, 5) . ':00',
                 'ends_on' => $day, 'day' => null, 'week' => null, 'month' => null, 'stops_on' => null];
        list($eh, $em) = array_map('intval', explode(':', $endsAt));
        $vars['ends_at'] = sprintf('%02d:%02d:%02d', $eh, $em, $em == 0 ? 0 : 59);
        if (!empty($payload['stops_on'])) {
            $stops = DateTime::createFromFormat('!Y-m-d', (string) $payload['stops_on']);
            if (!$stops || $stops->getTimestamp() <= $startsOn->getTimestamp()) {
                throw new Exception('Must be in the future');
            }
            $vars['stops_on'] = $stops->format('Y-m-d H:i:s');
        }
        $get = function ($k) use ($payload) { return isset($payload[$k]) ? (int) $payload[$k] : null; };
        switch ($repeats) {
            case 'weekly':
                $vars['day'] = $get('day');
                break;
            case 'monthly':
                if ($get('week')) {
                    $vars['week'] = $get('week');
                    $vars['day'] = $get('day');
                } else {
                    $vars['day'] = (int) $startsOn->format('d');   // the day of the month is the start date's
                    if ($get('day') && $get('day') !== $vars['day']) {
                        throw new Exception('The day of the month must be the start date\'s.');
                    }
                }
                break;
            case 'yearly':
                if ($get('week')) {
                    $vars['week'] = $get('week');
                    $vars['day'] = $get('day');
                    $vars['month'] = $get('month');
                } else {
                    $vars['day'] = (int) $startsOn->format('d');
                    $vars['month'] = (int) $startsOn->format('m');
                    if (($get('day') && $get('day') !== $vars['day']) || ($get('month') && $get('month') !== $vars['month'])) {
                        throw new Exception('The date must be the start date\'s.');
                    }
                }
                break;
            case 'never': case 'daily': case 'weekdays': case 'weekends':
                break;
            default:
                throw new Exception("Unknown repeat: {$repeats}");
        }
        return $vars;
    }

    public static function createEntry($payload, $staff_id) {
        $schedule = self::requireSchedule($payload['schedule_id'] ?? 0);
        $vars = self::entryVars($payload);
        $errors = [];
        if (!$schedule->isEntryUnique($vars, $errors)) {
            throw new Exception('Entry was not added: ' . self::errorText($errors));
        }
        $vars['schedule_id'] = $schedule->getId();
        $entry = ScheduleEntry::create($vars);
        if (!$entry || !$entry->getId()) {
            throw new Exception('Entry was not stored.');
        }
        return ['entry_id' => $entry->getId(), 'schedule_id' => $schedule->getId()];
    }

    public static function updateEntry($payload, $staff_id) {
        $schedule = self::requireSchedule($payload['schedule_id'] ?? 0);
        $entry = ScheduleEntry::lookup((int) ($payload['entry_id'] ?? 0));
        if (!$entry || (int) $entry->getScheduleId() !== (int) $schedule->getId()) {
            throw new Exception('Entry not found: ' . (int) ($payload['entry_id'] ?? 0));
        }
        $vars = self::entryVars($payload);
        $vars['id'] = $entry->getId();
        $errors = [];
        if (!$schedule->isEntryUnique($vars, $errors)) {
            throw new Exception('Entry was not updated: ' . self::errorText($errors));
        }
        foreach ($vars as $k => $v) {
            $entry->set($k, $v);
        }
        if (!$entry->save()) {
            throw new Exception('Entry could not be saved.');
        }
        return ['entry_id' => $entry->getId()];
    }

    public static function deleteEntry($payload, $staff_id) {
        $schedule = self::requireSchedule($payload['schedule_id'] ?? 0);
        $entry = ScheduleEntry::lookup((int) ($payload['entry_id'] ?? 0));
        if (!$entry || (int) $entry->getScheduleId() !== (int) $schedule->getId()) {
            throw new Exception('Entry not found: ' . (int) ($payload['entry_id'] ?? 0));
        }
        $id = $entry->getId();
        if (!$entry->delete()) {
            throw new Exception('Entry could not be deleted.');
        }
        return ['entry_id' => $id];
    }
}
}
