# TV Wake-on-LAN fix and "play the news" — 27 Nov 2026

## Problem
"Olive, turn the TV on" reported that it sent a wake signal but the TV did not
come online. The PC has three IPv4 interfaces (Wi-Fi 192.168.1.86, VirtualBox
192.168.56.1, WSL 192.168.48.1); the unbound limited broadcast could leave the
wrong adapter, and the post-wake check only ran `adb get-state` without
reconnecting.

## Fix (`tv.py`)
- `wake_on_lan()` now determines the source IP for the TV's host with a UDP
  probe, binds the socket to that interface, and sends the magic packet to the
  host (ports 9 and 7), the subnet broadcast and limited broadcast.
- `_port_open()` quickly checks ADB port 5555 so a sleeping TV is detected
  without waiting through adb's retry chain.
- `power_on()` goes straight to WoL when the port is closed instead of trying
  ADB keys first.
- `_wait_awake()` reconnects ADB after the wake signal instead of only polling
  `get-state`.

## New: "play the news"
New `news.play` intent plays a live news stream on the TV via YouTube:
- "play the news" → latest news live
- "play rai news" → Rai News 24
- "play CNN" / "CBS" / "BBC" / "Fox" → that broadcaster's live stream
The TV is woken first (ADB or WoL), then `adb_view_url()` opens the resolved
YouTube watch URL in the Android TV YouTube app. If the TV cannot be woken,
the stream opens on the computer instead. Plain "what's the news" / "headlines"
still speaks a written summary (unchanged `news` intent).

## Verification — live, 27 Nov 2026
- WoL sent bound to 192.168.1.86 woke the TV in ~2 s (ARP entry appeared).
- "olive turn tv on please" → "Turned on the TV with Wake-on-LAN."
- "olive turn tv off please" → "Turned off the TV."
- "play the news" / "play rai news" / "play cnn" → `news.play`, each replied
  "Playing … on the TV"; `tv status` confirmed "Bedroom TV is showing YouTube".
- "what are the headlines" still resolved to `news` and spoke a summary.
- 12/12 tests pass; hub healthy; TV returned to off afterwards.

## Notes
- TV MAC came from the already-learned `tv_mac.json` (no new config needed).
- All streams are public YouTube live feeds; no paid service.
