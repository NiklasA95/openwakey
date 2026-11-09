import contextlib
import io
import unittest

from openwakey import cli, protocol
from openwakey.protocol import Alarm

SERIAL = "TESTSERIAL000001"


def hex_frame(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


# Device-info reply captured from a Wakey (firmware 0.6.0), serial number replaced.
DEVICE_INFO_REPLY = hex_frame(
    "09 ff 03 00 01 01 83 35 00 00 30 2e 36 2e 30" + SERIAL.encode().hex()
    + "01 01 00 00 08 07 00 00 00 01 d6 29 02 26 25 d6 29 04 95 99 c7 61"
    + "09 ff 03 01 01 01 83 24 00 01 1b 00 95 99 c7 61 00 46 f3 f0 6a 01 00 07 14 1f 00 26 25 05 05"
    + "41 6c 61 72 6d"
    + "09 ff 03 02 01 01 83 16 00 03 2e 22 30 2a 05 01 00 15 00 07 00 00")


class RequestFrameTest(unittest.TestCase):
    """Frames that a real Wakey accepted."""

    def test_display(self):
        self.assertEqual(protocol.set_display(False), hex_frame("08 ee 00 00 00 01 85 0a 00 00"))

    def test_brightness_auto(self):
        self.assertEqual(protocol.set_brightness(auto=True), hex_frame("08 ee 00 00 00 01 82 0c 00 01 01 00"))

    def test_fm_on(self):
        self.assertEqual(protocol.fm_on(10710), hex_frame("08 ee 00 00 00 03 81 0c 00 01 d6 29"))

    def test_fm_favourites(self):
        self.assertEqual(protocol.set_fm_favourites([9510, 10710]),
                         hex_frame("08 ee 00 00 00 03 83 0d 00 26 25 d6 29"))

    def test_sleep_timer_length(self):
        self.assertEqual(protocol.set_sleep_timer_length(1800), hex_frame("08 ee 00 00 00 04 82 0b 00 08 07"))

    def test_request_without_payload_has_length_nine(self):
        self.assertEqual(protocol.request_sleep_timer(), hex_frame("08 ee 00 00 00 04 01 09 00"))

    def test_rejects_out_of_range_values(self):
        with self.assertRaises(ValueError):
            protocol.set_alarm_volume(32)
        with self.assertRaises(ValueError):
            protocol.fm_on(12000)


class AlarmRecordTest(unittest.TestCase):
    def test_saved_alarm_decodes_back(self):
        alarm = Alarm(id=1234567890, hour=6, minute=45, days=protocol.WEEKDAYS, fm_freq=9510,
                      snooze_minutes=9, snooze_repeats=2, name="Wörk", updated=1700000000)
        record = protocol.save_alarm(alarm, update=True)[9:]
        body = (len(record) + 3).to_bytes(2, "little") + record
        self.assertEqual(protocol.decode_alarm(body), alarm)

    def test_tone_alarm(self):
        alarm = Alarm(id=1, hour=23, minute=59, tone=7, name="", updated=1)
        record = protocol.save_alarm(alarm, update=False)[9:]
        decoded = protocol.decode_alarm((len(record) + 3).to_bytes(2, "little") + record)
        self.assertEqual((decoded.tone, decoded.fm_freq, decoded.name), (7, None, ""))


class ReplyTest(unittest.TestCase):
    def test_parse_skips_noise_and_keeps_partial_frame(self):
        reply = hex_frame("09 ff 00 00 01 01 85 0a 00 01")
        responses, rest = protocol.parse_responses(b"\x00\x01" + reply + reply[:4])
        self.assertEqual(len(responses), 1)
        self.assertEqual((responses[0].ok, responses[0].command, responses[0].payload), (True, (1, 0x85), b"\x01"))
        self.assertEqual(rest, reply[:4])

    def test_device_info(self):
        responses, rest = protocol.parse_responses(DEVICE_INFO_REPLY)
        self.assertEqual(rest, b"")
        self.assertTrue(protocol.device_info_complete(responses))
        info = protocol.decode_device_info(responses)
        self.assertEqual((info.firmware, info.serial), ("0.6.0", SERIAL))
        self.assertEqual((info.auto_brightness, info.brightness_level), (True, 1))
        self.assertEqual(info.sleep_timer, protocol.SleepTimer(False, 1800, 0))
        self.assertEqual((info.fm_on, info.fm_volume, info.fm_last_freq), (False, 1, 10710))
        self.assertEqual(info.fm_favourites, [9510, 10710])
        self.assertEqual(info.fm_band, (8750, 10800, 50))
        self.assertEqual((info.alarm_volume, info.next_alarm_id), (4, 1640470933))
        self.assertEqual((info.display_on, info.night_mode_on), (True, False))
        self.assertEqual((info.night_start, info.night_end), ((21, 0), (7, 0)))
        self.assertEqual(info.alarms, [Alarm(id=1640470933, hour=7, minute=20, days=protocol.WEEKDAYS,
                                             enabled=True, fm_freq=9510, snooze_minutes=5,
                                             snooze_repeats=5, name="Alarm", updated=1794175814)])

    def test_device_info_incomplete(self):
        responses, _ = protocol.parse_responses(DEVICE_INFO_REPLY[:53])
        self.assertFalse(protocol.device_info_complete(responses))

    def test_sleep_timer_query_reply_counts_half_seconds(self):
        self.assertEqual(protocol.decode_sleep_timer(hex_frame("01 00 2c 01 4d 02")),
                         protocol.SleepTimer(True, 300, 294.5))

    def test_sleep_timer_push(self):
        self.assertEqual(protocol.decode_sleep_timer(hex_frame("00 08 07 00 00")),
                         protocol.SleepTimer(False, 1800, 0))


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_prints_frame(self):
        code, out, _ = self.run_cli("--dry-run", "display", "off")
        self.assertEqual(code, 0)
        self.assertIn("08 ee 00 00 00 01 85 0a 00 00", out)

    def test_dry_run_alarm_add(self):
        code, out, err = self.run_cli("--dry-run", "alarm-add", "6:45", "--days", "sat", "--fm", "95.1",
                                      "--snooze", "9", "--repeats", "2", "--name", "Test")
        self.assertEqual(code, 0, err)
        frame = hex_frame(out.splitlines()[0])
        decoded = protocol.decode_alarm((len(frame) - 9 + 3).to_bytes(2, "little") + frame[9:])
        self.assertEqual((decoded.hour, decoded.minute, decoded.days, decoded.fm_freq),
                         (6, 45, protocol.SAT, 9510))
        self.assertEqual((decoded.snooze_minutes, decoded.snooze_repeats, decoded.name), (9, 2, "Test"))

    def test_dry_run_every_write_command(self):
        for argv in (["sync-time"], ["brightness", "3"], ["night-mode", "on", "22:00-06:30"],
                     ["alarm-on", "1"], ["alarm-off", "1"], ["alarm-delete", "1"], ["alarm-volume", "4"],
                     ["fm-on", "101.7"], ["fm-off"], ["fm-volume", "1"], ["fm-seek", "down"],
                     ["fm-favourites", "95.1", "107.1"], ["sleep-timer", "30"], ["sleep-start"],
                     ["sleep-stop"], ["sleep-status"], ["info"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli("--dry-run", *argv)
                self.assertEqual(code, 0, err)
                self.assertTrue(out.startswith("08 ee 00 00 00"), out)

    def test_dry_run_refuses_commands_that_read_first(self):
        code, _, err = self.run_cli("--dry-run", "alarm-edit", "1", "--time", "7:00")
        self.assertEqual(code, 1)
        self.assertIn("--dry-run", err)

    def test_days(self):
        self.assertEqual(cli.parse_days("mon,wed,sun"), protocol.MON | protocol.WED | protocol.SUN)
        self.assertEqual(cli.format_days(protocol.WEEKEND), "weekend")
        self.assertEqual(cli.format_days(protocol.MON | protocol.FRI), "Mon,Fri")

    def test_mhz(self):
        self.assertEqual(cli.parse_mhz("104.35"), 10435)
        self.assertEqual(cli.format_mhz(10435), "104.35")
        self.assertEqual(cli.format_mhz(10800), "108")


if __name__ == "__main__":
    unittest.main()
