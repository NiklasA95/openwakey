# OpenWakey

Control a **Soundcore Wakey** (model A3300) alarm clock from your Mac, without the Soundcore app:
alarms, clock, display, FM radio and sleep timer, from the terminal.

```
$ openwakey info
Device:       Soundcore Wakey, firmware 0.6.0, serial XXXXXXXXXXXXXXXX
Display:      on, brightness auto
Night mode:   off (21:00-07:00)
FM radio:     off, 107.1 MHz, volume 1, favourites 95.1, 107.1
Sleep timer:  off, length 30 min
Alarm volume: 4 of 31
Alarms:
  1640470933  07:20  weekdays      FM 95.1 MHz     snooze 5 min x5  on   'Alarm'   <- next
```

> Unofficial project, not affiliated with or endorsed by Anker or Soundcore. It talks to the clock
> using a protocol reverse engineered for interoperability; use it at your own risk.

## Requirements

- A Mac with Bluetooth. Tested on macOS 27 with the system Python 3.9.
- Python 3.9 or newer. If `python3 --version` asks you to install developer tools, accept, or run
  `xcode-select --install`.
- A Soundcore Wakey, paired with the Mac.

## Install

```sh
git clone https://github.com/NiklasA95/openwakey.git
cd openwakey
python3 -m venv .venv
.venv/bin/pip install .
```

This installs the `openwakey` command into `.venv/bin/`. To call it as plain `openwakey`, either add that folder
to your `PATH` or install with [pipx](https://pipx.pypa.io/) instead: `pipx install .`

## Set up the Wakey

1. Put the Wakey into Bluetooth pairing mode (see its manual) and pair it in
   **System Settings → Bluetooth**. You only do this once.
2. Close the Soundcore app on your phone, or switch off the phone's Bluetooth. The Wakey accepts only one
   control connection at a time.
3. Run `openwakey devices`. It should list your Wakey.
4. Run `openwakey info`. The first time, macOS asks whether your terminal app may use Bluetooth. Allow it.

## Usage

| Task | Command |
|---|---|
| Show settings and alarms | `openwakey info` (add `--json` for machine-readable output) |
| Set the clock to the Mac's time | `openwakey sync-time` (add `--12h` for a 12-hour display) |
| Display on/off | `openwakey display on`, `openwakey display off` |
| Brightness | `openwakey brightness auto`, `openwakey brightness 1` … `5` |
| Night mode (display off during a window) | `openwakey night-mode on 22:00-06:30`, `openwakey night-mode off` |
| Add an alarm | `openwakey alarm-add 6:45 --days weekdays --tone 3 --name Work` |
| Wake to the radio | `openwakey alarm-add 8:00 --days sat,sun --fm 95.1` |
| Change an alarm | `openwakey alarm-edit <id> --time 7:00` (options you leave out stay as they are) |
| Switch an alarm on/off | `openwakey alarm-on <id>`, `openwakey alarm-off <id>` (also stops a ringing alarm) |
| Delete an alarm | `openwakey alarm-delete <id>` |
| Alarm volume (all alarms) | `openwakey alarm-volume 0` … `31` |
| FM radio | `openwakey fm-on 101.7`, `openwakey fm-on` (last station), `openwakey fm-off` |
| FM volume | `openwakey fm-volume 0` … `31` |
| Find the next station | `openwakey fm-seek` or `openwakey fm-seek down` |
| Favourite stations | `openwakey fm-favourites 95.1 101.7 107.1` (replaces the list) |
| Sleep timer | `openwakey sleep-timer 30`, `openwakey sleep-start`, `openwakey sleep-stop`, `openwakey sleep-status` |

Alarm ids are shown by `openwakey info`. Alarm options:

- `--days`: `mon` … `sun` (comma separated), `weekdays`, `weekend`, `daily` or `once` (default)
- `--tone 1-10` or `--fm MHZ`
- `--snooze MIN` (snooze length) and `--repeats N` (how often snooze repeats)
- `--name TEXT`
- `--disabled`: add the alarm switched off (`alarm-add` only)

Run `openwakey -h` or `openwakey <command> -h` for details.

Global options:

- `--address AA-BB-CC-DD-EE-FF` picks a Wakey when several are paired.
- `-v` prints the raw bytes sent and received.
- `--dry-run` prints the request without connecting.

You can also run the tool as `python3 -m openwakey`.

## Good to know

- The "Superior Sleep" sounds in the Soundcore app are played by the phone. The Wakey itself only runs
  the countdown, so `sleep-start` doesn't play anything.
- Night mode stores a new time window only while it is being switched on.
- The Soundcore app keeps its own copy of your alarms and syncs with the clock when it connects.
  Alarms you add here are stored on the Wakey itself.

## Troubleshooting

| Message | What to do |
|---|---|
| `Bluetooth is off` | Turn Bluetooth on. |
| `No paired Wakey found` | Pair the Wakey in System Settings → Bluetooth, then run `openwakey devices`. |
| `Could not connect` | Make sure the Wakey is switched on and close to the Mac. |
| `Could not open the control channel` | Close the Soundcore app on your phone. Check System Settings → Privacy & Security → Bluetooth and allow your terminal app. |
| `The Wakey did not answer` | Try again. If it keeps happening, run the command with `-v` and open an issue with the output. |

## Status

Every feature above has been tested on a real Wakey (firmware 0.6.0). Other firmware versions may
behave differently. [PROTOCOL.md](PROTOCOL.md) documents the Bluetooth protocol, including what was
verified and how.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests
```

- `src/openwakey/protocol.py`: frame builders and reply decoders, platform independent.
- `src/openwakey/macos.py`: the macOS Bluetooth transport (IOBluetooth via PyObjC).
- `src/openwakey/cli.py`: the `openwakey` command.

The protocol module has no macOS dependency, so a transport for Linux (RFCOMM sockets) or other
platforms can be added next to `macos.py`.

## License

[MIT](LICENSE)
