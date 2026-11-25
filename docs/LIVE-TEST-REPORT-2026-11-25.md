# Live alarm / reminder / message test report — 25 Nov 2026

Machine-readable evidence: `LIVE-TEST-EVIDENCE-2026-11-25.json`.
All tests ran locally against the live hub and the real speaker.

## Scope note (honest)
The "spoken requests" were injected as transcribed text through the hub's
`/command` endpoint. The **wake-word and Whisper ASR stages were not exercised**
in these tests — the evidence field `recognised_transcript` is the text we sent,
not a microphone transcription. Intent classification, handler selection,
persistence, scheduling, delivery and TTS were all exercised for real.

## Test A — alarm end-to-end
- Request: "Olive, set an alarm for two minutes from now"
- Intent: **`alarm.set`** (never `timer.set`)
- Handler: `h_alarm_set` → scheduler record `kind=alarm`
- Creation confirmation: *"Alarm set for 11:48."*
- Saved record: due `2026-11-25T11:48:29+01:00`, trace `4a36ccbe4efd`, timezone Europe/Rome
- Scheduler registered it; log line:
  `scheduled alarm id=… trace=4a36ccbe4efd due=2026-11-25T11:48:29+01:00 kind=alarm`
- Delivery at `11:48:30` (≈1 s after due), trace `4a36ccbe4efd`,
  `delivered=true`, speaker `LibreEcho`, voice `this is ricky`,
  spoken text: *"Good morning. It is 11:48. Your alarm is going off."*
- Alarm auto-removed after delivery; no duplicate traces.
- Separate cancellation check: created a +10-minute alarm (`Cancelled 1 alarm.`
  on `cancel all alarms`), pending count 1 → 0. **PASS**

## Test B — reminder end-to-end with restart
- Request: "Olive, remind me in three minutes to check the reminder system"
- Intent: **`reminder.set`**
- Handler: `h_reminder_set` → scheduler record `kind=reminder`
- Creation confirmation: *"Reminder set for 11:51: check the reminder system."*
- Saved record: due `2026-11-25T11:51:37+01:00`, trace `d619fdf6c832`
- Hub restarted while the reminder was pending. After restart the same record
  was still present with the same trace and due time.
- Delivery at `11:51:39`, `delivered=true`, speaker `LibreEcho`,
  voice `this is ricky`, spoken text: *"Reminder: check the reminder system."*
- Log: `delivery trace=d619fdf6c832 kind=reminder ok=True voice=this is ricky`.
- No repeated delivery; no silent loss. **PASS**

## Test C — test message / notification
- Connected channels checked first:
  - WhatsApp bridge: `status=starting`, stale Chrome-lock error — **not connected**,
    no authorised external destination, so **no external message was sent**.
  - Local speaker notification channel: connected and used.
- Request: "test the notifications" → intent `system.notifications.test`,
  creation confirmation: *"Notification test queued, trace e13beb32411e."*
- Delivery at `11:51:51`, trace `e13beb32411e`, `delivered=true`,
  speaker `LibreEcho`, voice `this is ricky`, spoken: *"This is a notification test."*
- Only reported delivered because the delivery log confirms an actual announce
  API success, not merely because the request was accepted. **PASS (local channel)**

## Confirmation separation
| Phase | Alarm | Reminder | Test notification |
|---|---|---|---|
| Created | "Alarm set for 11:48." | "Reminder set for 11:51: …" | "Notification test queued, trace …" |
| Executed | "Your alarm is going off." | "Reminder: check the reminder system." | "This is a notification test." |

Failures are honest: exceptions are logged with the trace ID and the delivery
record gets `delivered=false` plus an error string.

## Acceptance criteria
- Alarm requests never entered the timer pipeline — verified (`alarm.set`, typed `alarm` record).
- Alarm and reminder creation persisted — verified in `olive_schedule.json` / dashboard.
- Both triggered at the expected time (within ~2 s) — verified.
- Both spoken through configured voice (`this is ricky`, Piper via router) — verified in delivery log.
- Creation vs delivery confirmations distinguishable — verified.
- Message sending verified, not assumed — local delivery confirmed; WhatsApp not connected, nothing external sent.
- Failed actions produce honest failure confirmations — code path present and exercised in automated tests.
- Logs provide full evidence — trace IDs present for request, scheduling and delivery.

## Final state
Hub healthy, 111 intents, 9/9 services. Pending events: 0.
Duplicate traces: none. Evidence file and this report are in the project root.

## Remaining limitations
- ASR/wake stages not part of these injected-text tests.
- No real external messaging destination is authorised or connected.
- Acoustic playback is inferred from successful speaker API delivery, not recorded audio.
- Fix applied during testing: word-number durations in reminders
  ("in three minutes …") are now stripped from the reminder text correctly.
