# Taking the LibreEcho speaker away from home

The speaker currently points at `192.168.1.86` (the PC's LAN address). That only
works while both devices are on the same home network. Verified from the
speaker itself:

```
http://192.168.1.86:8765/health  -> 200
http://100.108.79.37:8765/health -> 000   (tailnet not reachable from home WiFi)
```

The PC already has Tailscale with the stable address **100.108.79.37**. The hub
listens on `0.0.0.0:8765` and `0.0.0.0:10300`, so it is ready to accept a
tailnet connection. The missing piece is giving the speaker a route to the
tailnet.

## Recommended: travel router running Tailscale

LibreEcho OS 0.14.5 on this speaker is a minimal ARMv7 Linux with no package
manager, no root/sudo and no Tailscale client, so Tailscale cannot be installed
on the speaker itself. Use a small travel router instead (GL.iNet and similar
models support Tailscale directly):

1. In Tailscale admin, generate an **auth key** (prefer reusable, tagged, with
   expiry) for the travel router.
2. On the travel router: enable Tailscale, paste the auth key, join the same
   tailnet as the PC.
3. Connect the speaker's Wi-Fi to the travel router's network. The speaker will
   obtain a normal local IP from the router, and the router routes `100.x` to
   the tailnet.
4. Repoint the speaker at the PC's tailnet address:

   ```powershell
   .\switch-olive-endpoint.ps1 -Endpoint 100.108.79.37
   ```

   This updates the assistant `base_url` to `http://100.108.79.37:8765/v1` and
   the Wyoming STT URI to `tcp://100.108.79.37:10300`, then restarts the voice
   pipeline.

5. Test: say *"what time is it?"*. The hub log should show a chat request from
   the speaker and the reply should be spoken.
6. When back home, either keep using the travel router or switch back:

   ```powershell
   .\switch-olive-endpoint.ps1 -Endpoint 192.168.1.86
   ```

Preview without changing anything:

```powershell
.\switch-olive-endpoint.ps1 -Endpoint 100.108.79.37 -WhatIfOnly
```

## Alternative: DDNS + port forwarding (less secure)

You can forward TCP 8765 and 10300 on the home router and use a dynamic DNS
hostname with `-Endpoint yourname.duckdns.org`. Be aware:

- `/v1/...` requires `HUB_TOKEN`, so 8765 is reasonably protected.
- The Wyoming ASR proxy on **10300 has no authentication** — anyone who finds
  the open port can send audio. Only do this behind a VPN or an IP allowlist.
- Residential ISPs may block inbound ports or change CGNAT addresses.
- Music Assistant (8095) would also need a secure tunnel; native radio fallback
  works without it.

The Tailscale travel-router approach avoids all of these issues.

## What still works away from home

| Feature | Away from home |
|---|---|
| Wake word, local timers/alarms, LED, device TTS | Yes — entirely on speaker |
| Time, date, weather, news, currency | Yes — hub + internet |
| Calendar, memory, shopping list, routines | Yes — through the hub |
| YouTube on TV, Netflix, TV status | Only if that TV is reachable |
| Native radio / fallback radio | Yes — through the speaker's internet |
| Music Assistant named stations | No, unless 8095 is reachable over the VPN; native fallback is used |
| Local IoT (kitchen light) | No — those devices stay at home |
| Tuya cloud | Currently unusable (suspended project) |

## Firewall checklist

The hub already binds all interfaces. On the PC, allow inbound TCP 8765 and
10300 from the Tailscale interface (Windows Firewall "Private" profile is
usually enough; generic Python rules exist). Confirm from the speaker:

```sh
curl -s -o /dev/null -w "%{http_code}" --max-time 6 http://100.108.79.37:8765/health
```

A `200` means the route and firewall are correct; after that the switch script
is all that is needed.
