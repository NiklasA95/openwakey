"""macOS transport: talks to a paired Wakey over an IOBluetooth RFCOMM channel."""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

import IOBluetooth
import objc
from Foundation import NSDate, NSObject, NSRunLoop

from . import protocol

POWER_ON = 1
SERVICE_UUID_BYTES = bytes.fromhex(protocol.SERVICE_UUID.replace("-", ""))
RFCOMM_PROTOCOL_UUID = bytes.fromhex("00000003")
SDP_UUID_TYPE = 3
SDP_TIMEOUT_SECONDS = 10
SETTLE_SECONDS = 0.3


class WakeyError(Exception):
    """A problem the user can act on; the message says what to do."""


def pump(seconds: float) -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def normalise_address(address: str) -> str:
    return address.strip().lower().replace(":", "-")


def _require_bluetooth_on() -> None:
    controller = IOBluetooth.IOBluetoothHostController.defaultController()
    if controller is None or controller.powerState() != POWER_ON:
        raise WakeyError("Bluetooth is off. Turn it on in System Settings → Bluetooth.")


def paired_devices() -> List[object]:
    _require_bluetooth_on()
    return list(IOBluetooth.IOBluetoothDevice.pairedDevices() or [])


def _sdp_bytes(element) -> bytes:
    if element is None or element.getTypeDescriptor() != SDP_UUID_TYPE:
        return b""
    return bytes(element.getValue())


def _wakey_channel(services) -> Optional[int]:
    for record in services or []:
        if _sdp_bytes(record.getAttributeDataElement_(0x0001)) != SERVICE_UUID_BYTES:
            continue
        protocols = record.getAttributeDataElement_(0x0004)
        for entry in (protocols.getArrayValue() if protocols else None) or []:
            parts = entry.getArrayValue() or []
            if len(parts) >= 2 and _sdp_bytes(parts[0]) == RFCOMM_PROTOCOL_UUID:
                return int(parts[1].getValue())
    return None


def is_wakey(device) -> bool:
    name = str(device.name() or "")
    return "wakey" in name.lower() or _wakey_channel(device.services()) is not None


def find_wakey(address: Optional[str] = None):
    devices = paired_devices()
    if address:
        wanted = normalise_address(address)
        for device in devices:
            if normalise_address(str(device.addressString())) == wanted:
                return device
        raise WakeyError(f"No paired Bluetooth device with address {address}. "
                         "Run `openwakey devices` to list paired Wakeys.")
    wakeys = [d for d in devices if is_wakey(d)]
    if not wakeys:
        raise WakeyError("No paired Wakey found. Hold the Bluetooth button on the Wakey until it "
                         "beeps, then pair it in System Settings → Bluetooth.")
    if len(wakeys) > 1:
        listed = ", ".join(f"{d.name()} ({d.addressString()})" for d in wakeys)
        raise WakeyError(f"Several Wakeys are paired: {listed}. Choose one with --address.")
    return wakeys[0]


def _discover_channel(device) -> int:
    channel = _wakey_channel(device.services())
    if channel is not None:
        return channel
    previous_update = device.getLastServicesUpdate()
    device.performSDPQuery_(None)
    deadline = time.monotonic() + SDP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        pump(0.2)
        if device.getLastServicesUpdate() != previous_update:
            channel = _wakey_channel(device.services())
            if channel is not None:
                return channel
    raise WakeyError(f"{device.name()} does not offer the Wakey control service. "
                     "Is this really a Soundcore Wakey?")


class WakeyChannelDelegate(NSObject):
    def init(self):
        self = objc.super(WakeyChannelDelegate, self).init()
        if self is not None:
            self.buffer = bytearray()
            self.closed = False
        return self

    def rfcommChannelData_data_length_(self, channel, data, length):
        self.buffer.extend(bytes(data[:length]))

    def rfcommChannelClosed_(self, channel):
        self.closed = True


Predicate = Callable[[Sequence[protocol.Response]], bool]


class Wakey:
    """An open control connection. Use as a context manager."""

    dry_run = False

    def __init__(self, address: Optional[str] = None):
        self.device = find_wakey(address)
        self.name = str(self.device.name())
        self.channel = None
        self.delegate = WakeyChannelDelegate.alloc().init()

    def __enter__(self) -> "Wakey":
        if not self.device.isConnected() and self.device.openConnection() != 0:
            raise WakeyError(f"Could not connect to {self.name}. Make sure it is switched on and nearby.")
        channel_id = _discover_channel(self.device)
        status, self.channel = self.device.openRFCOMMChannelSync_withChannelID_delegate_(
            None, channel_id, self.delegate)
        if status != 0 or self.channel is None:
            raise WakeyError(
                f"Could not open the control channel on {self.name} (error {status & 0xFFFFFFFF:#x}). "
                "Close the Soundcore app on your phone, and allow your terminal to use Bluetooth in "
                "System Settings → Privacy & Security → Bluetooth.")
        return self

    def __exit__(self, *exc_info) -> None:
        if self.channel is not None:
            self.channel.closeChannel()
            pump(SETTLE_SECONDS)

    def request(self, frame: bytes, done: Optional[Predicate] = None,
                timeout: float = 3.0) -> List[protocol.Response]:
        """Send a request and collect replies until `done` holds or `timeout` expires.

        By default this waits for a reply to the same command. Pushed messages that arrive
        meanwhile (such as the next-alarm notification) are returned as well.
        """
        expected = protocol.command_of(frame)
        done = done or (lambda responses: any(r.command == expected for r in responses))
        self.delegate.buffer.clear()
        status = self.channel.writeSync_length_(frame, len(frame))
        if status != 0:
            raise WakeyError(f"Sending to {self.name} failed (error {status & 0xFFFFFFFF:#x}).")
        responses: List[protocol.Response] = []
        pending = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.delegate.closed:
            pump(0.05)
            if self.delegate.buffer:
                pending += bytes(self.delegate.buffer)
                self.delegate.buffer.clear()
                parsed, pending = protocol.parse_responses(pending)
                responses += parsed
                if done(responses):
                    pump(SETTLE_SECONDS)
                    extra, _ = protocol.parse_responses(pending + bytes(self.delegate.buffer))
                    self.delegate.buffer.clear()
                    return responses + extra
        if self.delegate.closed:
            raise WakeyError(f"{self.name} closed the connection.")
        return responses
