# Soundcore Wakey (A3300) protocol

Decompiled from the soundcore Android app 2.1.2 (2020, not packed).
Sources: `com.oceanwing.soundcore.spp.a3300.{a,b,c}` (A3300CmdConstants,
A3300DeviceManager, A3300Dispatch), `com.oceanwing.soundcore.spp.a`
(BaseBtDeviceCommand), `com.oceanwing.spp.d` (SppLink), `spp.h` (ParseData).

## Transport

Classic Bluetooth RFCOMM (SPP), insecure socket, service UUID
`0CF12D31-FAC3-4553-BD80-D6832E7B3300`. The app connects with
`createInsecureRfcommSocketToServiceRecord` and resolves the channel via SDP.

## Framing (no checksum in this version)

Request (app -> Wakey):

    08 EE 00 00 00 <group> <cmd> <len_lo> <len_hi> <payload...>

`len` = total frame length including the 7-byte header and the 2 length bytes.
A command with no payload is therefore `08 EE 00 00 00 gg cc 09 00`.

Response (Wakey -> app):

    09 FF <total> <index> <status> <group> <cmd> <len_lo> <len_hi> <payload...>

- `status` (byte 4): `01` = success.
- `total`/`index` (bytes 2,3): only used by the multi-packet device-info reply (01/83).
- Frames whose first two bytes are `01 FE` are passed through raw (OTA path).

All multi-byte integers are little-endian. FM frequencies are in 10 kHz
units (e.g. 10170 = 101.70 MHz; valid range 7600..10800).

## Commands

| Grp/Cmd | Name | Request payload | Response payload (after byte 9) |
|---|---|---|---|
| 01/81 | Sync time | `yy-2000, MM, dd, HH, mm, ss, is24h` | – |
| 01/82 | Brightness | `auto, level, countdown` (auto 1 = auto brightness; level 1..5 when auto is 0; countdown is legacy, the app sends 0 for firmware >= 0.4.9) | – |
| 01/83 | Get device info / sync alarms | `00` then for each known alarm `id(u32) updateTime(u32)` | multi-packet blob, see below |
| 01/84 | Shut down Wakey | – | – |
| 01/85 | LED display on/off | `on` | `on` |
| 01/86 | Night mode | `on, startH, startM, endH, endM` | `isEffective` |
| 02/82 | Get alarm volume | – | `vol` (0..31) |
| 02/83 | Set alarm volume | `vol` (0..31) | – |
| 02/84 | Enable/disable alarm | `on, id(u32)` | `on, id(u32), updateTime(u32)` |
| 02/85 | Delete alarm | `id(u32)` | – |
| 02/86 | Preview tone | `01, tone` or FM: `00, freq(u16)` | – |
| 02/87 | Stop preview | – | – |
| 02/88 | Get next alarm | – | `id(u32)` |
| 02/89 | Add/update alarm | alarm record (below) | – |
| 03/81 | FM off / on+tune | off: `00`; on: `01, freq(u16)` | `on, freq(u16)` |
| 03/83 | Set favourite stations | `freq(u16)` × n | – |
| 03/84 | Set FM volume | `vol` (0..31) | – |
| 03/86 | Seek station | `01` = next, `00` = previous | `finished, freq(u16)` |
| 03/88 | (FM, no payload) | – | – |
| 03/89 | Get FM volume | – | `vol` |
| 04/01 | Get sleep-timer info | – | `on, total(u16), remain(u16)` (seconds) |
| 04/81 | Start/stop sleep timer | `on, 00` | `on` |
| 04/82 | Set sleep-timer length | `seconds(u16)` | – |
| 05/01 | Button-press statistics | – | 13 × u16: alarm short/long, BT short/long, vol+ short/long, vol- short/long, FM short/long, D short/long, USB plug |
| 05/02 | (stats, probably reset) | – | – |

"Superior Sleep" sounds are played by the phone over A2DP; the Wakey only
runs the countdown timer (04/xx).

## Alarm record (02/89 request, also inside the device-info blob)

    id(u32)            // app uses unix seconds at creation
    updateType(u8)     // 0 = add, 1 = update, 2 = delete
    updateTime(u32)    // unix seconds
    isOn(u8)
    hourSystem(u8)     // 1 = 24h, 0 = 12h
    hour(u8) min(u8)   // hour is always 0..23
    week(u8)           // bitmask: Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64; 0 = once
    toneType(u8)       // 1 = built-in tone, 0 = FM station
    tone(u8)           // if toneType == 1 (1..10)
    | fmFreq(u16)      // if toneType == 0
    repeat(u8)         // snooze repeat count
    snooze(u8)         // snooze minutes
    name(UTF-8, rest of frame)

## Device-info blob (01/83 reply)

Reassemble all packets (`total` at byte 2, `index` at byte 3) by concatenating
each packet from byte 7 onward. The result is a list of TLV-ish sections:

    sectionLen(u16) type(u8) body[sectionLen-10]   // advance by sectionLen-7

- type 0 – base info: firmware (5 ASCII), SN (16 ASCII), backlight
  `isOn, mode, countdown`, sleep timer `on, total(u16), remain(u16)`,
  FM `on, vol, lastFreq(u16), count, freq(u16)×count`,
  then `alarmVolume, nextAlarmId(u32)`.
- type 1 – one alarm record (prefixed by its own u16 length), same layout as above.
- type 2 – deleted alarms: `len(u16)` then `id(u32)` × n.
- type 3 – extra: FM `min(u16) max(u16) step(u8)`, LED `on`, night mode
  `on, startH, startM, endH, endM, isEffective`.

## Verified on a real Wakey (firmware 0.6.0, BT 1.1.11), 2026-11-09

- No checksum in either direction; the frame format above is correct.
- 01/83 device info, 01/85 LED on/off (display visibly toggled), 02/89 add
  alarm and 02/85 delete alarm work; results confirmed by reading 01/83 back.
- 01/85 only gets a reply when it changes the state; asking for the state the
  display is already in gets no reply at all.
- 04/01 sleep-timer info: the direct query reply payload is 6 bytes,
  `on, 00, total(u16, seconds), remain(u16)`, instead of the 5 the 2020 app
  expects. While running, `remain` counts down at about 2 per second, i.e.
  half-seconds (5-min timer: 589 after 7 s, 555 after 25 s).
- Sleep timer control: 04/82 sets the length in seconds, 04/81 `01 00` starts
  and `00 00` stops it; the reply echoes `on`. Verified with a 5-min timer.
- 01/81 sync time: the display shows the sent time immediately. The hour is
  always 0..23; the last byte switches the clock face (1 = 24h, 0 = 12h).
  The alarm record's `hourSystem` does not affect the display (a 24h clock
  had an alarm stored with `hourSystem = 0`).
- 01/82 brightness: manual levels 5, 1 and 3 visibly change the display
  (5 brightest, 1 dimmest); `auto` restores automatic brightness.
- 01/86 night mode: enabling it with a window that contains the current time
  replies `isEffective = 1` and the Wakey also pushes 01/85 `00` (display off);
  disabling it pushes 01/85 `01`. The window is only stored when night mode is
  switched on; sending it with `on = 0` leaves the previous window in place.
- FM: 03/84 volume, 03/81 on/tune/off all confirmed by reply. 03/86 seek
  streams one `00, freq` reply per scanned step (wrapping 108.0 -> 87.5),
  then `01, freq` when a station is found, followed by a 03/81 `01, freq`.
  03/83 favourites: the list is replaced wholesale and its length can change
  (3 stations stored, then back to 2), confirmed via 01/83.
  Turning FM on also pushes 04/01 sleep-timer info with a 5-byte payload
  (the direct 04/01 query reply has 6 bytes).
- 02/83 alarm volume: set to 1 and back to 4, confirmed via 01/83.
- 02/84 alarm on/off: reply is `on, id(u32), updateTime(u32)`, followed by a
  02/88 push with the new next-alarm id. Sent while the alarm was ringing, it
  stopped the alarm immediately, and the reply carried a new updateTime (the
  Wakey stamps the alarm when it fires). Alarm volume 1 is quiet but audible.
- 02/89 edit: resending a record with an existing id and `updateType = 1`
  replaces it in place (time, days, tone <-> FM, snooze, name all changed;
  no duplicate). The stored record keeps `updateType = 0` and takes the sent
  updateTime.
- The Wakey stores `hourSystem = 0` in alarm records even when 1 is sent.
- 02/88 is pushed by the Wakey after alarm changes (`id` of the next alarm);
  sending it as a query gets no reply. 02/82 and 03/89 also get no reply.

## Caveats

- The exact meaning of 03/88 and 05/02 is inferred, not confirmed.
