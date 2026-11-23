"""Drive the reference emulator (an isolated PPSSPP copy) through its WebSocket debugger.

PPSSPP runs the same disc image as Yakumo, so its RAM and screen are the reference for a
recompiled run. Only the copy under atelier-psprecomp/ppsspp-diff is started, with its own
memstick; the launcher instance in remake-ppsspp and its saves are never used.
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import secrets
import socket
import struct
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
HOME = PROJECT / "atelier-psprecomp" / "ppsspp-diff"
EXE = HOME / "PPSSPPWindows64.exe"
DISC = PROJECT / "game-mhfu" / "disc.iso"
PORT = 45695
BUTTONS = {"cross", "circle", "triangle", "square", "up", "down", "left", "right", "start", "select",
           "ltrigger", "rtrigger"}


class WebSocket:
    """Just enough of RFC 6455 for one local text-frame conversation."""

    def __init__(self, host, port, path, timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: debugger.ppsspp.org\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk: raise ConnectionError("debugger closed the handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]: raise ConnectionError(head.split(b"\r\n", 1)[0].decode())
        self.buffer = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk: raise ConnectionError("debugger closed the connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def send(self, text):
        data = text.encode()
        mask = secrets.token_bytes(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                 bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                 bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        message = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126: n = struct.unpack(">H", self._read(2))[0]
            elif n == 127: n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            opcode = b0 & 0x0F
            if opcode == 8: raise ConnectionError("debugger closed the connection")
            if opcode == 9: self.sock.sendall(bytes([0x8A, 0x80]) + secrets.token_bytes(4)); continue
            message += payload
            if b0 & 0x80: return message.decode()

    def close(self):
        try: self.sock.close()
        except OSError: pass


class Link:
    """Requests with tickets; unrelated events that arrive meanwhile are kept in `events`."""

    def __init__(self, port=PORT, timeout=10.0):
        self.ws = WebSocket("127.0.0.1", port, "/debugger", timeout)
        self.tickets = itertools.count(1)
        self.events = []

    def call(self, event, **params):
        ticket = str(next(self.tickets))
        self.ws.send(json.dumps({"event": event, "ticket": ticket, **params}))
        while True:
            reply = json.loads(self.ws.recv())
            # cpu.stepping / cpu.resume answer with an untagged broadcast of the same name
            if reply.get("ticket") == ticket or (event.startswith("cpu.") and reply.get("event") == event
                                                 and "ticket" not in reply):
                if reply.get("event") == "error": raise RuntimeError(f"{event}: {reply.get('message')}")
                return reply
            self.events.append(reply)

    def read(self, address, size):
        reply = self.call("memory.read", address=address, size=size)
        return base64.b64decode(reply["base64"])

    def press(self, button, frames=6):
        if button not in BUTTONS: raise ValueError(f"unknown PSP button {button}")
        self.call("input.buttons.press", button=button, duration=frames)

    def paused(self):
        """Context manager: the CPU stepping, as screenshots and consistent multi-range reads need."""
        link = self
        class Paused:
            def __enter__(self): link.call("cpu.stepping"); return link
            def __exit__(self, *exc): link.call("cpu.resume")
        return Paused()

    def close(self):
        self.ws.close()

    def screenshot(self, path, pid=None):
        """The debugger's own capture, else the emulator window's client area (it must be visible)."""
        try:
            with self.paused():
                reply = self.call("gpu.buffer.screenshot", type="base64")
            Path(path).write_bytes(base64.b64decode(reply["base64"]))
        except RuntimeError:
            if pid is None: raise
            window_capture(pid).save(path)
        return path


def window_capture(pid):
    import ctypes
    from ctypes import wintypes
    from PIL import ImageGrab
    user32 = ctypes.windll.user32
    found = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd): found.append(hwnd)
        return True
    user32.EnumWindows(each, 0)
    if not found: raise RuntimeError("no visible PPSSPP window")
    rect = wintypes.RECT(); user32.GetClientRect(found[0], ctypes.byref(rect))
    corner = wintypes.POINT(0, 0); user32.ClientToScreen(found[0], ctypes.byref(corner))
    return ImageGrab.grab((corner.x, corner.y, corner.x + rect.right, corner.y + rect.bottom), all_screens=True)



def prepare(ms0: Path):
    """Replace the reference memstick's saves by a private copy; never touches game-mhfu/ms0."""
    import shutil
    target = HOME / "memstick" / "PSP" / "SAVEDATA"
    if (PROJECT / "game-mhfu" / "ms0").resolve() in [ms0.resolve(), *ms0.resolve().parents]:
        raise ValueError("the reference reads a private copy, not game-mhfu/ms0")
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(ms0 / "PSP" / "SAVEDATA", target)


def start(timeout=60.0):
    if not EXE.exists(): raise FileNotFoundError(EXE)
    process = subprocess.Popen([str(EXE), str(DISC)], cwd=str(HOME))
    deadline = time.monotonic() + timeout
    while True:
        try:
            return process, Link()
        except OSError:
            if process.poll() is not None: raise RuntimeError("PPSSPP exited during start")
            if time.monotonic() > deadline:
                process.kill(); raise TimeoutError("PPSSPP debugger did not open")
            time.sleep(0.5)


def stop(process, link=None):
    if link: link.close()
    if process.poll() is None:
        process.terminate()
        try: process.wait(10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(10)
