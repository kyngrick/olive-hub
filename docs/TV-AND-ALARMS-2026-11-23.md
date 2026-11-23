# Full TV control, YouTube playback and the alarm problem

Built and verified 23 Nov 2026.

## 1. Turning the TV on when it is off

`tv.py` now has a layered wake chain:

1. **ADB already reachable** (TV in light standby): send `KEYCODE_WAKE` (224),
   then `KEYCODE_POWER` (26) as fallback.
2. **Wake-on-LAN**: send a magic packet to the TV's MAC on UDP 9 (broadcast +
   unicast), then poll ADB for up to 25 s while it boots.
3. **Chromecast / HDMI-CEC**: start the default receiver as a last resort.
4. If nothing works, the spoken reply explains why instead of staying silent.

The TV's MAC is **learned automatically** on the next successful ADB
connection and cached in `tv_mac.json` next to `tv.py`. It can also be set
explicitly with `TV_MAC=xx:xx:xx:xx:xx:xx` in `.env` (editable from the
dashboard's Configuration card). On this machine it learned
`ec:9c:32:72:9a:67` for `192.168.1.41`.

`adb_view_url()` (used by YouTube) now calls `ensure_awake()` first, so a media
command works when the screen is off.

`_adb_connect()` retries `adb connect` + `get-state` three times; this fixed a
transient failure that made "turn off the TV" report the wrong result.

Verified end-to-end:
```
Alexa, turn off the tv    -> "Turned off the TV."      (ADB state: Asleep)
Alexa, turn on the tv     -> "Turned on the TV."       (ADB state: Awake)
Alexa, turn off the tv    -> "Turned off the TV."
Alexa, show me al jazeera live on youtube
                          -> "Playing 'al jazeera live' on the TV."
                             (ADB focus: com.google.android.youtube.tv, Awake)
```

**Caveat:** Wake-on-LAN on many TVs only works over wired Ethernet. If the
TV is on Wi-Fi and completely powered down, no software on the PC can wake it —
you need HDMI-CEC from an always-on device, a smart plug, or a TV that supports
Wi-Fi WoL. In standby (the normal state after "turn off the TV") ADB wake works
reliably, as verified above.

## 2. YouTube commands

`h_youtube` now:
- accepts **show me / find / watch / play / put on** plus **"on YouTube"**;
- accepts **"… music video(s)"** even when the word YouTube is omitted;
- strips only the command shell, keeping the useful search words:
  - "show me al jazeera live on youtube" → `al jazeera live`
  - "play any jamaican dancehall music video" → `jamaican dancehall music video`
- wakes the TV first, resolves a real video through `web_helpers.youtube_url`,
  opens it with an Android `VIEW` intent in the TV YouTube app, and falls back
  to the PC browser with an honest spoken message if the TV cannot be reached.

Verified:
- `al jazeera live` → `https://www.youtube.com/watch?v=gCNeDWCI0vo&autoplay=1`
  (first result, resolved)
- `jamaican dancehall music video` → `https://www.youtube.com/watch?v=Lq7x1v86uEM&autoplay=1`
- Both commands returned `Playing … on the TV` and the TV's foreground app
  became `com.google.android.youtube.tv`.

Because YouTube's result page is scraped, the first result may occasionally be
a promo or playlist rather than the exact clip; saying a more specific query
gives better results.

## 3. "Set alarm for tomorrow at 8 am" → "Timer for how long?"

**Root cause found — it is not the hub.** The hub's own handler was tested and
correctly returns `Alarm set for Tuesday 08:00` for that exact phrase, and
`parse_when()` resolves tomorrow 08:00 correctly. The reply comes from
**LibreEcho OS 0.14.5 firmware**: 0.14.x has local voice *timers*, but native
voice *alarms* were only added in **0.14.6** (release notes: "Alarms with repeat
days and snooze, and spoken reminders"). The device's local timer parser grabs
"set … alarm … for …" and asks for a duration because it cannot parse a clock
time as a duration.

What was changed on our side to make alarms work reliably:

1. **ASR correction** (`correct_common_asr`): when the transcript contains
   "timer" *and* a day/time word (`tomorrow`, `tonight`, `morning`, `8 am`,
   `at 8`, `domani`, …), "timer" is rewritten to "alarm" before routing. This
   fixes the common Whisper mishearing of "alarm" as "timer".
2. **Whisper prompt** extended with full example phrases:
   "Set alarm for tomorrow at 8 am. Wake me up at 7 tomorrow morning. Set an
   alarm for 6 pm. Play Al Jazeera live on YouTube. …"
3. **More alarm phrasings** accepted by the hub, so you can bypass the
   firmware's timer parser:
   - `Alexa, wake me at 8 tomorrow morning`
   - `Alexa, alarm at 8 tomorrow`
   - `Alexa, set an alarm for tomorrow at 8 am`

All three were verified through `/command` after a hub restart: each returned
`alarm.set: Alarm set for Tuesday 08:00`.

**Reliable workaround until you upgrade:** say **"Alexa, wake me at 8 tomorrow
morning"** — it contains neither "set" nor "timer", so the firmware timer
parser does not intercept it and it reaches the hub. Or upgrade to LibreEcho
0.14.6, which has native alarms and fixes this at the source.

(While verifying, test alarms were cleared with "cancel all alarms"; that also
cleared any older alarm records that were present.)

## 4. Restart / deployment notes

- `start-hub-detached.ps1` now also recycles **Whisper** on every hub start,
  so prompt/model changes load without manually killing the elevated ASR
  process (same trick already used for the dashboard).
- `TV_MAC` added to `.env.example`.
- Hub, Whisper and dashboard were restarted and verified healthy after the
  changes.
