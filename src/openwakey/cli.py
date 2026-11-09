"""`openwakey` command line tool: control a Soundcore Wakey alarm clock from a Mac."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from typing import List, Optional, Sequence, Tuple

from . import __version__, protocol
from .protocol import Alarm, DeviceInfo, Response

DAY_WORDS = {"mon": protocol.MON, "tue": protocol.TUE, "wed": protocol.WED, "thu": protocol.THU,
             "fri": protocol.FRI, "sat": protocol.SAT, "sun": protocol.SUN,
             "weekdays": protocol.WEEKDAYS, "weekend": protocol.WEEKEND,
             "daily": protocol.DAILY, "once": 0}

EXAMPLES = """\
examples:
  openwakey info
  openwakey sync-time
  openwakey alarm-add 6:45 --days weekdays --tone 3 --name Work
  openwakey alarm-edit 1700000000 --time 7:00
  openwakey fm-on 101.7
  openwakey brightness auto
"""


class WakeyError(Exception):
    pass


# Argument types -------------------------------------------------------------------------------

def parse_time(value: str) -> Tuple[int, int]:
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {value!r}") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise argparse.ArgumentTypeError(f"invalid time {value!r}")
    return hour, minute


def parse_window(value: str) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    start, separator, end = value.partition("-")
    if not separator:
        raise argparse.ArgumentTypeError(f"expected HH:MM-HH:MM, got {value!r}")
    return parse_time(start), parse_time(end)


def parse_days(value: str) -> int:
    mask = 0
    for word in value.lower().split(","):
        if word.strip() not in DAY_WORDS:
            raise argparse.ArgumentTypeError(
                f"unknown day {word!r}; use mon..sun, weekdays, weekend, daily or once")
        mask |= DAY_WORDS[word.strip()]
    return mask


def parse_volume(value: str) -> int:
    level = int(value)
    if not 0 <= level <= protocol.MAX_VOLUME:
        raise argparse.ArgumentTypeError(f"volume must be 0..{protocol.MAX_VOLUME}")
    return level


def parse_mhz(value: str) -> int:
    try:
        freq = round(float(value) * 100)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a frequency in MHz, got {value!r}") from None
    if not protocol.FM_MIN_FREQ <= freq <= protocol.FM_MAX_FREQ:
        raise argparse.ArgumentTypeError("frequency must be between 76 and 108 MHz")
    return freq


def parse_on_off(value: str) -> bool:
    if value.lower() not in ("on", "off"):
        raise argparse.ArgumentTypeError("use on or off")
    return value.lower() == "on"


def parse_brightness(value: str) -> Optional[int]:
    if value.lower() == "auto":
        return None
    if value in ("1", "2", "3", "4", "5"):
        return int(value)
    raise argparse.ArgumentTypeError("use auto or a level from 1 (dim) to 5 (bright)")


# Formatting -----------------------------------------------------------------------------------

def format_mhz(freq: int) -> str:
    return f"{freq / 100:.2f}".rstrip("0").rstrip(".")


def format_days(mask: int) -> str:
    named = {0: "once", protocol.DAILY: "daily", protocol.WEEKDAYS: "weekdays", protocol.WEEKEND: "weekend"}
    if mask in named:
        return named[mask]
    return ",".join(name for name, bit in protocol.DAY_NAMES if mask & bit)


def format_hm(value: Optional[Tuple[int, int]]) -> str:
    return f"{value[0]:02d}:{value[1]:02d}" if value else "?"


def format_alarm(alarm: Alarm, next_id: int = 0) -> str:
    sound = f"FM {format_mhz(alarm.fm_freq)} MHz" if alarm.fm_freq else f"tone {alarm.tone}"
    parts = [f"{alarm.id:>10}", f"{alarm.hour:02d}:{alarm.minute:02d}", f"{format_days(alarm.days):<12}",
             f"{sound:<14}", f"snooze {alarm.snooze_minutes} min x{alarm.snooze_repeats}",
             "on " if alarm.enabled else "off", repr(alarm.name) if alarm.name else ""]
    line = "  ".join(parts).rstrip()
    return line + ("   <- next" if alarm.id == next_id and alarm.enabled else "")


def print_info(name: str, info: DeviceInfo) -> None:
    brightness = "auto" if info.auto_brightness else f"level {info.brightness_level} of 5"
    rows = [("Device", f"{name}, firmware {info.firmware}, serial {info.serial}"),
            ("Display", f"{'on' if info.display_on else 'off'}, brightness {brightness}")]
    if info.night_mode_on is not None:
        state = "on" if info.night_mode_on else "off"
        active = ", active now" if info.night_mode_active else ""
        rows.append(("Night mode", f"{state} ({format_hm(info.night_start)}-{format_hm(info.night_end)}){active}"))
    favourites = ", ".join(format_mhz(f) for f in info.fm_favourites) or "none"
    rows.append(("FM radio", f"{'on' if info.fm_on else 'off'}, {format_mhz(info.fm_last_freq)} MHz, "
                             f"volume {info.fm_volume}, favourites {favourites}"))
    if info.sleep_timer:
        timer = info.sleep_timer
        state = f"running, {timer.remaining_seconds / 60:.0f} min left" if timer.running else "off"
        rows.append(("Sleep timer", f"{state}, length {timer.total_seconds // 60} min"))
    rows.append(("Alarm volume", f"{info.alarm_volume} of {protocol.MAX_VOLUME}"))
    for label, value in rows:
        print(f"{label + ':':<14}{value}")
    print("Alarms:" if info.alarms else "Alarms:       none")
    for alarm in sorted(info.alarms, key=lambda a: (a.hour, a.minute)):
        print("  " + format_alarm(alarm, info.next_alarm_id))


def to_json(info: DeviceInfo) -> str:
    return json.dumps(dataclasses.asdict(info), indent=2, ensure_ascii=False)


# Clients --------------------------------------------------------------------------------------

class DryRunClient:
    name = "Wakey"
    dry_run = True

    def request(self, frame: bytes, done=None, timeout: float = 3.0) -> List[Response]:
        print(frame.hex(" "))
        group, cmd = protocol.command_of(frame)
        return [Response(True, group, cmd, b"", 0, 0, b"")]


class VerboseClient:
    dry_run = False

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name

    def request(self, frame: bytes, done=None, timeout: float = 3.0) -> List[Response]:
        print(">>", frame.hex(" "), file=sys.stderr)
        responses = self.inner.request(frame, done, timeout)
        for response in responses:
            print("<<", response.raw.hex(" "), file=sys.stderr)
        return responses


def send(client, frame: bytes, what: str, allow_silence: bool = False, **kwargs) -> List[Response]:
    responses = client.request(frame, **kwargs)
    expected = protocol.command_of(frame)
    reply = next((r for r in responses if r.command == expected), None)
    if reply is None and allow_silence:
        return responses
    if reply is None:
        raise WakeyError(f"The Wakey did not answer ({what}).")
    if not reply.ok:
        raise WakeyError(f"The Wakey rejected the request ({what}).")
    return responses


def read_info(client) -> DeviceInfo:
    if client.dry_run:
        raise WakeyError("This command needs to read the Wakey first, so it cannot run with --dry-run.")
    responses = send(client, protocol.request_device_info(), "read state",
                     done=protocol.device_info_complete, timeout=5.0)
    if not protocol.device_info_complete(responses):
        raise WakeyError("The Wakey sent an incomplete state reply. Try again.")
    return protocol.decode_device_info(responses)


def find_alarm(info: DeviceInfo, alarm_id: int) -> Alarm:
    for alarm in info.alarms:
        if alarm.id == alarm_id:
            return alarm
    known = ", ".join(str(a.id) for a in info.alarms) or "none"
    raise WakeyError(f"No alarm with id {alarm_id}. Alarm ids on this Wakey: {known}.")


# Commands -------------------------------------------------------------------------------------

def cmd_devices(args) -> None:
    from . import macos
    devices = macos.paired_devices()
    wakeys = [d for d in devices if macos.is_wakey(d)]
    if not wakeys:
        print("No paired Wakey found. Pair it in System Settings → Bluetooth first.")
        return
    for device in wakeys:
        state = "connected" if device.isConnected() else "not connected"
        print(f"{device.name()}  {device.addressString()}  ({state})")


def cmd_info(client, args) -> None:
    if client.dry_run:
        client.request(protocol.request_device_info())
        return
    info = read_info(client)
    if args.json:
        print(to_json(info))
    else:
        print_info(client.name, info)


def format_clock(moment: time.struct_time, twelve_hour: bool) -> str:
    if not twelve_hour:
        return f"{moment.tm_hour:02d}:{moment.tm_min:02d}:{moment.tm_sec:02d}"
    hour = moment.tm_hour % 12 or 12
    suffix = "AM" if moment.tm_hour < 12 else "PM"
    return f"{hour}:{moment.tm_min:02d}:{moment.tm_sec:02d} {suffix}"


def cmd_sync_time(client, args) -> None:
    now = time.time()
    send(client, protocol.sync_time(is24h=not args.twelve_hour, when=now), "set time")
    display = "12-hour" if args.twelve_hour else "24-hour"
    print(f"Clock set to {format_clock(time.localtime(now), args.twelve_hour)} ({display} display).")


def cmd_display(client, args) -> None:
    responses = send(client, protocol.set_display(args.state), "display", allow_silence=True,
                     timeout=1.5)
    state = "on" if args.state else "off"
    if responses or client.dry_run:
        print(f"Display {state}.")
        return
    # The Wakey only answers when the state changes, so silence may mean "already like that".
    if read_info(client).display_on != args.state:
        raise WakeyError("The Wakey did not answer (display).")
    print(f"Display already {state}.")


def cmd_brightness(client, args) -> None:
    level = args.level
    send(client, protocol.set_brightness(auto=level is None, level=level or 1), "brightness")
    print("Brightness set to auto." if level is None else f"Brightness set to level {level} of 5.")


def cmd_night_mode(client, args) -> None:
    window = args.window
    if window is None:
        info = read_info(client)
        window = (info.night_start or (22, 0), info.night_end or (7, 0))
    start, end = window
    if args.window is not None and not args.state:
        print("Note: the Wakey only stores a new window while switching night mode on.")
    responses = send(client, protocol.set_night_mode(args.state, start, end), "night mode")
    reply = next(r for r in responses if r.command == (0x01, 0x86))
    active = " It is active now, so the display is off." if reply.payload[:1] == b"\x01" else ""
    print(f"Night mode {'on' if args.state else 'off'} ({format_hm(start)}-{format_hm(end)}).{active}")


def cmd_alarm_add(client, args) -> None:
    hour, minute = args.time
    alarm = Alarm(id=int(time.time()), hour=hour, minute=minute, days=args.days, enabled=not args.disabled,
                  tone=args.tone or 1, fm_freq=args.fm, snooze_minutes=args.snooze_minutes,
                  snooze_repeats=args.snooze_repeats, name=args.name)
    send(client, protocol.save_alarm(alarm, update=False), "add alarm")
    print(f"Added alarm {alarm.id}:")
    print("  " + format_alarm(alarm))


def cmd_alarm_edit(client, args) -> None:
    alarm = find_alarm(read_info(client), args.id)
    if args.time:
        alarm.hour, alarm.minute = args.time
    if args.days is not None:
        alarm.days = args.days
    if args.tone is not None:
        alarm.tone, alarm.fm_freq = args.tone, None
    if args.fm is not None:
        alarm.fm_freq = args.fm
    for attribute in ("name", "snooze_minutes", "snooze_repeats"):
        value = getattr(args, attribute)
        if value is not None:
            setattr(alarm, attribute, value)
    alarm.updated = int(time.time())
    send(client, protocol.save_alarm(alarm, update=True), "edit alarm")
    print("Alarm updated:")
    print("  " + format_alarm(alarm))


def cmd_alarm_enable(client, args, enabled: bool) -> None:
    send(client, protocol.set_alarm_enabled(args.id, enabled), "switch alarm")
    print(f"Alarm {args.id} {'on' if enabled else 'off'}.")


def cmd_alarm_delete(client, args) -> None:
    send(client, protocol.delete_alarm(args.id), "delete alarm")
    print(f"Alarm {args.id} deleted.")


def cmd_alarm_volume(client, args) -> None:
    send(client, protocol.set_alarm_volume(args.level), "alarm volume")
    print(f"Alarm volume set to {args.level} of {protocol.MAX_VOLUME}.")


def cmd_fm_on(client, args) -> None:
    freq = args.mhz or read_info(client).fm_last_freq
    send(client, protocol.fm_on(freq), "FM on")
    print(f"FM radio on, {format_mhz(freq)} MHz.")


def cmd_fm_off(client, args) -> None:
    send(client, protocol.fm_off(), "FM off")
    print("FM radio off.")


def cmd_fm_volume(client, args) -> None:
    send(client, protocol.set_fm_volume(args.level), "FM volume")
    print(f"FM volume set to {args.level} of {protocol.MAX_VOLUME}.")


def _seek_finished(responses: Sequence[Response]) -> bool:
    return any(r.command == protocol.FM_POWER or (r.command == protocol.FM_SEEK and r.payload[:1] == b"\x01")
               for r in responses)


def cmd_fm_seek(client, args) -> None:
    responses = send(client, protocol.fm_seek(up=args.direction == "up"), "FM seek",
                     done=_seek_finished, timeout=20.0)
    if client.dry_run:
        return
    found = [r for r in responses if r.command in (protocol.FM_SEEK, protocol.FM_POWER) and len(r.payload) >= 3]
    if not found or not _seek_finished(responses):
        raise WakeyError("The Wakey did not report a station. It may still be searching.")
    print(f"Tuned to {format_mhz(found[-1].payload[1] | found[-1].payload[2] << 8)} MHz.")


def cmd_fm_favourites(client, args) -> None:
    send(client, protocol.set_fm_favourites(args.mhz), "FM favourites")
    print("FM favourites: " + ", ".join(f"{format_mhz(f)} MHz" for f in args.mhz) + ".")


def cmd_sleep_timer(client, args) -> None:
    send(client, protocol.set_sleep_timer_length(args.minutes * 60), "sleep timer length")
    print(f"Sleep timer length set to {args.minutes} min.")


def cmd_sleep_run(client, args, running: bool) -> None:
    send(client, protocol.set_sleep_timer_running(running), "sleep timer")
    print(f"Sleep timer {'started' if running else 'stopped'}.")


def cmd_sleep_status(client, args) -> None:
    responses = send(client, protocol.request_sleep_timer(), "sleep timer status")
    if client.dry_run:
        return
    reply = next(r for r in responses if r.command == protocol.SLEEP_TIMER_INFO)
    timer = protocol.decode_sleep_timer(reply.payload)
    if timer.running:
        print(f"Sleep timer running: {timer.remaining_seconds / 60:.1f} of {timer.total_seconds // 60} min left.")
    else:
        print(f"Sleep timer off, length {timer.total_seconds // 60} min.")


# Parser ---------------------------------------------------------------------------------------

def add_alarm_options(parser: argparse.ArgumentParser, editing: bool) -> None:
    default = None if editing else 0
    parser.add_argument("--days", type=parse_days, default=default,
                        help="mon..sun (comma separated), weekdays, weekend, daily or once")
    sound = parser.add_mutually_exclusive_group()
    sound.add_argument("--tone", type=int, choices=range(1, protocol.TONE_COUNT + 1), help="built-in tone 1-10")
    sound.add_argument("--fm", type=parse_mhz, metavar="MHZ", help="wake to an FM station instead of a tone")
    parser.add_argument("--name", default=None if editing else "")
    parser.add_argument("--snooze", dest="snooze_minutes", type=int, default=None if editing else 5,
                        metavar="MIN", help="snooze length in minutes")
    parser.add_argument("--repeats", dest="snooze_repeats", type=int, default=None if editing else 3,
                        metavar="N", help="how often snooze repeats")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openwakey", description="Control a Soundcore Wakey alarm clock.",
                                     epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--address", help="Bluetooth address of the Wakey, if several are paired")
    parser.add_argument("--dry-run", action="store_true", help="print the request bytes instead of sending")
    parser.add_argument("-v", "--verbose", action="store_true", help="print raw frames")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def command(name, handler, help_text, **kwargs):
        sub_parser = sub.add_parser(name, help=help_text, description=help_text, **kwargs)
        sub_parser.set_defaults(handler=handler)
        return sub_parser

    command("devices", None, "list paired Wakeys")
    command("info", cmd_info, "show settings and alarms").add_argument("--json", action="store_true")
    command("sync-time", cmd_sync_time, "set the clock to this Mac's time").add_argument(
        "--12h", dest="twelve_hour", action="store_true", help="use a 12-hour display")
    command("display", cmd_display, "switch the display on or off").add_argument("state", type=parse_on_off)
    command("brightness", cmd_brightness, "set brightness: auto or 1-5").add_argument(
        "level", type=parse_brightness, metavar="auto|1-5")
    night = command("night-mode", cmd_night_mode, "keep the display off during a time window")
    night.add_argument("state", type=parse_on_off)
    night.add_argument("window", type=parse_window, nargs="?", metavar="HH:MM-HH:MM")

    add = command("alarm-add", cmd_alarm_add, "add an alarm")
    add.add_argument("time", type=parse_time, metavar="HH:MM")
    add_alarm_options(add, editing=False)
    add.add_argument("--disabled", action="store_true", help="add the alarm switched off")
    edit = command("alarm-edit", cmd_alarm_edit, "change an alarm; options you leave out stay as they are")
    edit.add_argument("id", type=int)
    edit.add_argument("--time", type=parse_time, metavar="HH:MM")
    add_alarm_options(edit, editing=True)
    command("alarm-on", lambda c, a: cmd_alarm_enable(c, a, True), "switch an alarm on").add_argument("id", type=int)
    command("alarm-off", lambda c, a: cmd_alarm_enable(c, a, False),
            "switch an alarm off (also stops it while ringing)").add_argument("id", type=int)
    command("alarm-delete", cmd_alarm_delete, "delete an alarm").add_argument("id", type=int)
    command("alarm-volume", cmd_alarm_volume, "set the volume of all alarms").add_argument(
        "level", type=parse_volume, metavar="0-31")

    command("fm-on", cmd_fm_on, "turn the radio on (last station if none given)").add_argument(
        "mhz", type=parse_mhz, nargs="?", metavar="MHZ")
    command("fm-off", cmd_fm_off, "turn the radio off")
    command("fm-volume", cmd_fm_volume, "set the radio volume").add_argument("level", type=parse_volume, metavar="0-31")
    command("fm-seek", cmd_fm_seek, "search for the next station").add_argument(
        "direction", choices=("up", "down"), nargs="?", default="up")
    command("fm-favourites", cmd_fm_favourites, "replace the favourite stations").add_argument(
        "mhz", type=parse_mhz, nargs="+", metavar="MHZ")

    command("sleep-timer", cmd_sleep_timer, "set the sleep timer length").add_argument("minutes", type=int)
    command("sleep-start", lambda c, a: cmd_sleep_run(c, a, True), "start the sleep timer")
    command("sleep-stop", lambda c, a: cmd_sleep_run(c, a, False), "stop the sleep timer")
    command("sleep-status", cmd_sleep_status, "show the sleep timer")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "devices":
            cmd_devices(args)
        elif args.dry_run:
            args.handler(DryRunClient(), args)
        else:
            from . import macos
            try:
                with macos.Wakey(args.address) as wakey:
                    args.handler(VerboseClient(wakey) if args.verbose else wakey, args)
            except macos.WakeyError as error:
                raise WakeyError(str(error)) from None
    except (WakeyError, ValueError) as error:
        print(f"openwakey: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
