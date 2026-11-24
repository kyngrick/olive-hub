# Alarm / Timer / Reminder / Calendar reliability repair — final report

Date 24 Nov 2026. Authorised implementation completed, tested and verified.

## 1. Root cause of "alarm requests go through the timer pipeline"

There were **three distinct defects**, which together produced the symptom:

1. **Alarms were implemented on top of device timers.** `h_alarm_set` called
   `ctx.device.add_timer(wait, label)` to make the speaker ring, and kept only a
   secondary hub scheduler record with an empty `announce` string. From the
   device's point of view an alarm literally *was* a timer, and the hub never
   spoke the alarm itself. This is the true architectural cause.
2. **A timezone bug crashed the alarm handler.** After the timezone patch,
   `parse_when()` returned an aware datetime but `h_alarm_set` still subtracted
   a naive `dt.datetime.now()`, raising
   `TypeError: can't subtract offset-naive and offset-aware datetimes`.
   The router converted the exception into "Sorry, that didn't work.", which
   users reasonably interpreted as the wrong pipeline.
3. **Reminder text extraction was unreliable** (`set a reminder for 10 minutes
   to check the oven` asked "What should I remind you about?", and
   `tomorrow at 9 am to call mum` produced "morrow to call mum"), so some
   requests fell through to the LLM, which then claimed it could not set them.

## 2. Implemented architecture

### Typed entities (single JSON store `olive_schedule.json`, no data deleted)
Each record now carries: `id`, `kind` (`alarm` | `timer` | `reminder` | `system`
| `briefing`), `due_at`, `label`, `announce`, `repeat` rule, `status`
(pending/delivering/delivered), `created_at`, `delivered_at`, `attempts`,
`delivery_error`, `trace_id`, `source`, `timezone`.
Migration is backwards-compatible — old rows load with defaults.

- **Alarm** → typed `alarm` record with its own spoken message
  ("Good morning. It is 07:00. Your alarm is going off."). **No device timer.**
- **Timer** → typed `timer` record with its own message
  ("Your 1 minute timer has finished."). **No alarm record.**
- **Reminder** → typed `reminder` record with the actual reminder text.
- Recurrence: daily / weekdays / weekends / weekly, rescheduled after delivery.

### Scheduler (`olive_hub.py`)
- 5-second tick (was 1 s; now intentional and documented).
- Awaited delivery under an `asyncio.Lock`, so one item cannot be delivered
  twice by concurrent ticks.
- Persisted delivery log: `olive_notifications.jsonl` with trace ID, due time,
  speaker, voice, attempts, success and error.
- Startup recovery: recent missed events (≤12 h) are spoken once with a
  "Sorry to interrupt." prefix; very old ones are discarded. Idempotence is
  enforced by `delivered_at` and status.
- Atomic file writes via temp file + `os.replace`.
- New **calendar monitor** task: syncs on start and every 10 minutes, gives a
  07:00 daily briefing, and announces appointments 15 minutes ahead once each
  (state in `olive_scheduler_state.json`).

### Request tracing
`Router.route()` generates a trace ID per voice request and logs it. Scheduled
events carry their own trace ID into the delivery log.

### New intents (111 total, previously 98)
`alarm.status`, `timer.status`, `reminder.status`,
`calendar.today/tomorrow/sync/briefing`,
`system.scheduler.status/test`, `system.notifications.status/test`,
`system.calendar.status`, `system.reminders.status`. All have real handlers.

### Dashboard
New **Scheduler / notifications** card with pending events, trace IDs, state and
the last 20 delivery records (`GET /api/scheduler`).

## 3. Files changed
- `olive_hub.py` — scheduler, delivery log, typed alarm/timer/reminder paths,
  calendar monitor, trace IDs, 13 new intents/handlers.
- `dashboard.py` — `/api/scheduler`, scheduler card.
- `tests/test_scheduler_reliability.py` — new automated regression suite.
- `WORKLOG.md`, this report.

No database deletion. No firmware change. No paid service. No external deploy.

## 4. Test results

### Automated (simulated short clock)
```
python tests/test_scheduler_reliability.py
Ran 6 tests ... OK
```
Covers: alarm/timer/reminder routing separation; alarm does not create timer
record; timer does not create alarm record; delivery + no duplicate; restart
recovery once; persistence across instances.

### Four requested real-world tests (live, with actual speaker)
| # | Test | Result |
|---|---|---|
| 1 | "set alarm for tomorrow at 8 am" | `alarm.set`, typed `alarm` record only — **PASS** |
| 2 | Reminder for +2 min, waited for speech | delivered at due time, trace `ab158bf5dc3d` — **PASS** |
| 3 | Timer for 1 min, waited for speech | `timer` delivery, independent, trace `feb590268872` — **PASS** |
| 4 | Restart with pending reminder, waited | survived restart, announced trace `b16d815f8b5a` — **PASS** |

Delivery log excerpts (real):
```
at=17:36:33 kind=reminder due=17:36:32 trace=ab158bf5dc3d delivered=True voice=this is ricky
at=17:37:38 kind=timer    due=17:37:36 trace=feb590268872 delivered=True voice=this is ricky
at=17:40:44 kind=reminder due=17:40:41 trace=b16d815f8b5a delivered=True voice=this is ricky
```
No trace ID appears more than once. Pending events after tests: 0.

### Other verification
- Health: `ok=true`, **111 intents**, **9/9 services**.
- Morning briefing fired once on scheduler start and was logged once.
- Calendar state keys present: `briefing:2026-11-24`, `last_calendar_sync`.
- All test alarms/reminders/timers were cancelled afterwards.

## 5. Remaining limitations (honest)
- Calendar **notification** logic was implemented and its state machine
  exercised, but not verified against a real 15-minute appointment because
  none exists in the connected calendar today. Daily briefing content is
  assembled from the real calendar provider but has only been observed on the
  automatic startup run, not at a real 07:00.
- `alarm.snooze` currently reschedules the hub alarm record; the device's own
  native alarm UI is not used (the firmware is 0.14.5, which lacks native alarms).
- Delivery verification means the hub successfully called the speaker's
  announce API. It does not capture acoustic output (no microphone loopback).
- JSON file storage is adequate for this single-user workload; SQLite was not
  introduced, avoiding a risky migration of user data.
- The speaker remains on 0.14.5 because 0.14.6 failed feature validation on
  this device (see `OTA-RECOVERY-2026-11-24.md`).

## 6. Rollback
Backup taken before changes: `olive_hub.py.bak-20261124-172228`.
To roll back: stop the hub, restore that file, also restore the previous
`dashboard.py` from git (`git checkout <previous-commit> -- dashboard.py`), and
restart. Schedule/delivery files are additive and can be left in place; the old
code ignores the new fields. No user data was deleted.
