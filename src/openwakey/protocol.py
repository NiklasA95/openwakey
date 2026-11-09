"""Soundcore Wakey (A3300) protocol: request builders and reply decoders.

Platform independent. The transport is a classic Bluetooth RFCOMM channel; see PROTOCOL.md.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional, Sequence, Tuple

SERVICE_UUID = "0CF12D31-FAC3-4553-BD80-D6832E7B3300"

REQUEST_HEADER = bytes([0x08, 0xEE, 0x00, 0x00, 0x00])
RESPONSE_MAGIC = bytes([0x09, 0xFF])
MIN_FRAME_LENGTH = 9

MON, TUE, WED, THU, FRI, SAT, SUN = 1, 2, 4, 8, 16, 32, 64
WEEKDAYS = MON | TUE | WED | THU | FRI
WEEKEND = SAT | SUN
DAILY = WEEKDAYS | WEEKEND
DAY_NAMES = (("Mon", MON), ("Tue", TUE), ("Wed", WED), ("Thu", THU),
             ("Fri", FRI), ("Sat", SAT), ("Sun", SUN))

MAX_VOLUME = 31
TONE_COUNT = 10
FM_MIN_FREQ = 7600   # 10 kHz units: 76.00 MHz
FM_MAX_FREQ = 10800  # 108.00 MHz

DEVICE_INFO = (0x01, 0x83)
FM_POWER = (0x03, 0x81)
FM_SEEK = (0x03, 0x86)
SLEEP_TIMER_INFO = (0x04, 0x01)
NEXT_ALARM_PUSH = (0x02, 0x88)


class Response(NamedTuple):
    ok: bool
    group: int
    cmd: int
    payload: bytes
    packet_count: int
    packet_index: int
    raw: bytes

    @property
    def command(self) -> Tuple[int, int]:
        return self.group, self.cmd


@dataclass
class Alarm:
    id: int
    hour: int
    minute: int
    days: int = 0
    enabled: bool = True
    tone: int = 1
    fm_freq: Optional[int] = None
    snooze_minutes: int = 5
    snooze_repeats: int = 3
    name: str = ""
    updated: int = 0


@dataclass
class SleepTimer:
    running: bool
    total_seconds: int
    remaining_seconds: float


@dataclass
class DeviceInfo:
    firmware: str = ""
    serial: str = ""
    auto_brightness: bool = True
    brightness_level: int = 1
    sleep_timer: Optional[SleepTimer] = None
    fm_on: bool = False
    fm_volume: int = 0
    fm_last_freq: int = 0
    fm_favourites: List[int] = field(default_factory=list)
    fm_band: Optional[Tuple[int, int, int]] = None
    alarm_volume: int = 0
    next_alarm_id: int = 0
    alarms: List[Alarm] = field(default_factory=list)
    deleted_alarm_ids: List[int] = field(default_factory=list)
    display_on: Optional[bool] = None
    night_mode_on: Optional[bool] = None
    night_start: Optional[Tuple[int, int]] = None
    night_end: Optional[Tuple[int, int]] = None
    night_mode_active: Optional[bool] = None


def frame(group: int, cmd: int, payload: bytes = b"") -> bytes:
    total_length = len(REQUEST_HEADER) + 2 + 2 + len(payload)
    return REQUEST_HEADER + bytes([group, cmd]) + struct.pack("<H", total_length) + payload


def command_of(request: bytes) -> Tuple[int, int]:
    return request[5], request[6]


def _check_range(name: str, value: int, low: int, high: int) -> int:
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _u32(value: int) -> bytes:
    return struct.pack("<I", value)


# Requests -------------------------------------------------------------------------------------

def sync_time(is24h: bool = True, when: Optional[float] = None) -> bytes:
    t = time.localtime(when)
    _check_range("year", t.tm_year, 2000, 2255)
    return frame(0x01, 0x81, bytes([t.tm_year - 2000, t.tm_mon, t.tm_mday,
                                    t.tm_hour, t.tm_min, t.tm_sec, int(is24h)]))


def request_device_info(known_alarms: Sequence[Tuple[int, int]] = ()) -> bytes:
    """`known_alarms` are (id, updated) pairs; the Wakey then only sends alarms that differ."""
    return frame(*DEVICE_INFO, b"\x00" + b"".join(_u32(i) + _u32(u) for i, u in known_alarms))


def set_brightness(auto: bool, level: int = 1) -> bytes:
    _check_range("brightness level", level, 1, 5)
    return frame(0x01, 0x82, bytes([int(auto), level, 0]))


def set_display(on: bool) -> bytes:
    return frame(0x01, 0x85, bytes([int(on)]))


def set_night_mode(on: bool, start: Tuple[int, int], end: Tuple[int, int]) -> bytes:
    return frame(0x01, 0x86, bytes([int(on), *start, *end]))


def set_alarm_volume(volume: int) -> bytes:
    return frame(0x02, 0x83, bytes([_check_range("volume", volume, 0, MAX_VOLUME)]))


def set_alarm_enabled(alarm_id: int, enabled: bool) -> bytes:
    return frame(0x02, 0x84, bytes([int(enabled)]) + _u32(alarm_id))


def delete_alarm(alarm_id: int) -> bytes:
    return frame(0x02, 0x85, _u32(alarm_id))


def save_alarm(alarm: Alarm, update: bool) -> bytes:
    """Add (`update=False`) or replace (`update=True`) an alarm. Hours are always 0..23."""
    _check_range("hour", alarm.hour, 0, 23)
    _check_range("minute", alarm.minute, 0, 59)
    _check_range("days", alarm.days, 0, DAILY)
    _check_range("snooze minutes", alarm.snooze_minutes, 0, 255)
    _check_range("snooze repeats", alarm.snooze_repeats, 0, 255)
    head = struct.pack("<IBIBBBBBB", alarm.id, 1 if update else 0, alarm.updated or int(time.time()),
                       int(alarm.enabled), 1, alarm.hour, alarm.minute, alarm.days,
                       0 if alarm.fm_freq else 1)
    if alarm.fm_freq:
        sound = _u16(_check_range("FM frequency", alarm.fm_freq, FM_MIN_FREQ, FM_MAX_FREQ))
    else:
        sound = bytes([_check_range("tone", alarm.tone, 1, TONE_COUNT)])
    snooze = bytes([alarm.snooze_repeats, alarm.snooze_minutes])
    return frame(0x02, 0x89, head + sound + snooze + alarm.name.encode("utf-8"))


def fm_on(freq: int) -> bytes:
    return frame(*FM_POWER, b"\x01" + _u16(_check_range("FM frequency", freq, FM_MIN_FREQ, FM_MAX_FREQ)))


def fm_off() -> bytes:
    return frame(*FM_POWER, b"\x00")


def set_fm_volume(volume: int) -> bytes:
    return frame(0x03, 0x84, bytes([_check_range("volume", volume, 0, MAX_VOLUME)]))


def fm_seek(up: bool = True) -> bytes:
    return frame(*FM_SEEK, bytes([int(up)]))


def set_fm_favourites(freqs: Sequence[int]) -> bytes:
    for freq in freqs:
        _check_range("FM frequency", freq, FM_MIN_FREQ, FM_MAX_FREQ)
    return frame(0x03, 0x83, b"".join(_u16(f) for f in freqs))


def request_sleep_timer() -> bytes:
    return frame(*SLEEP_TIMER_INFO)


def set_sleep_timer_length(seconds: int) -> bytes:
    return frame(0x04, 0x82, _u16(_check_range("sleep timer seconds", seconds, 1, 0xFFFF)))


def set_sleep_timer_running(running: bool) -> bytes:
    return frame(0x04, 0x81, bytes([int(running), 0]))


# Replies --------------------------------------------------------------------------------------

def parse_responses(buffer: bytes) -> Tuple[List[Response], bytes]:
    """Split a receive buffer into complete responses; returns them plus the unparsed rest."""
    responses = []
    while len(buffer) >= MIN_FRAME_LENGTH:
        if buffer[:2] != RESPONSE_MAGIC:
            next_start = buffer.find(RESPONSE_MAGIC, 1)
            buffer = buffer[next_start:] if next_start > 0 else b""
            continue
        length = struct.unpack_from("<H", buffer, 7)[0]
        if length < MIN_FRAME_LENGTH:
            buffer = buffer[2:]
            continue
        if len(buffer) < length:
            break
        raw, buffer = buffer[:length], buffer[length:]
        responses.append(Response(raw[4] == 1, raw[5], raw[6], raw[9:], raw[2], raw[3], raw))
    return responses, buffer


def device_info_complete(responses: Sequence[Response]) -> bool:
    packets = [r for r in responses if r.command == DEVICE_INFO]
    if not packets:
        return False
    expected = packets[0].packet_count or 1
    return len({r.packet_index for r in packets}) >= expected


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def decode_alarm(body: bytes) -> Alarm:
    record_length = _read_u16(body, 0)
    alarm = Alarm(id=_read_u32(body, 2), updated=_read_u32(body, 7), enabled=bool(body[11]),
                  hour=body[13], minute=body[14], days=body[15])
    if body[16] == 0:
        alarm.fm_freq = _read_u16(body, 17)
        offset = 19
    else:
        alarm.tone = body[17]
        offset = 18
    alarm.snooze_repeats, alarm.snooze_minutes = body[offset], body[offset + 1]
    alarm.name = body[offset + 2:record_length - 1].decode("utf-8", "replace")
    return alarm


def decode_sleep_timer(payload: bytes) -> SleepTimer:
    """The direct 04/01 reply has 6 bytes (remaining in half seconds), the pushed one 5 bytes."""
    if len(payload) >= 6:
        return SleepTimer(bool(payload[0]), _read_u16(payload, 2), _read_u16(payload, 4) / 2)
    return SleepTimer(bool(payload[0]), _read_u16(payload, 1), _read_u16(payload, 3))


def decode_device_info(responses: Sequence[Response]) -> DeviceInfo:
    packets = sorted((r for r in responses if r.command == DEVICE_INFO), key=lambda r: r.packet_index)
    blob = b"".join(r.raw[7:] for r in packets)
    info = DeviceInfo()
    offset = 0
    while offset + 3 <= len(blob):
        section_length, kind = _read_u16(blob, offset), blob[offset + 2]
        body = blob[offset + 3:offset + 3 + section_length - 10]
        if kind == 0:
            _decode_base_info(body, info)
        elif kind == 1:
            info.alarms.append(decode_alarm(body))
        elif kind == 2:
            info.deleted_alarm_ids += [_read_u32(body, i) for i in range(2, _read_u16(body, 0), 4)]
        elif kind == 3:
            _decode_extra_info(body, info)
        if section_length <= 7:
            break
        offset += section_length - 7
    return info


def _decode_base_info(body: bytes, info: DeviceInfo) -> None:
    info.firmware = body[0:5].decode("ascii", "replace")
    info.serial = body[5:21].decode("ascii", "replace")
    info.auto_brightness, info.brightness_level = bool(body[21]), body[22]
    info.sleep_timer = SleepTimer(bool(body[24]), _read_u16(body, 25), _read_u16(body, 27))
    info.fm_on, info.fm_volume, info.fm_last_freq = bool(body[29]), body[30], _read_u16(body, 31)
    favourite_count = body[33]
    info.fm_favourites = [_read_u16(body, 34 + n * 2) for n in range(favourite_count)]
    alarm_offset = 34 + favourite_count * 2
    info.alarm_volume = body[alarm_offset]
    info.next_alarm_id = _read_u32(body, alarm_offset + 1)


def _decode_extra_info(body: bytes, info: DeviceInfo) -> None:
    info.fm_band = (_read_u16(body, 0), _read_u16(body, 2), body[4] * 10)
    if len(body) >= 12:
        info.display_on = bool(body[5])
        info.night_mode_on = bool(body[6])
        info.night_start = (body[7], body[8])
        info.night_end = (body[9], body[10])
        info.night_mode_active = bool(body[11])
