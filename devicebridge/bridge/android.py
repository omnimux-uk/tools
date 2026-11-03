"""Android backend: scrcpy's server for the picture, adb for the touches.

scrcpy-server has a `raw_stream` mode that emits a bare H.264 Annex-B stream --
no container, no frame headers, no device metadata -- which is exactly what the
shared WebCodecs page expects, so both platforms render through the same code.

`adb screenrecord` can also produce Annex-B and needs nothing pushed to the
device, but measured 1185 ms of lag against scrcpy's 361 ms: the difference
between driving a phone and guessing at one. It is not used.

Input goes through one long-lived `adb shell`. A fresh `adb shell input tap`
per touch measured 34 ms; holding the shell open takes that to 16 ms, because
the cost is adb's per-invocation setup rather than anything on the device.

Android's `input` works in pixels, so the page's coordinate space is the
physical resolution from `wm size` -- unlike iOS, where idb wants points.
"""

import os
import random
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

LOG_TAG = "devicebridge.android"
REMOTE_JAR = "/data/local/tmp/scrcpy-server.jar"

NAV = ('<button data-btn="BACK">‹</button>'
       '<button data-btn="HOME">⌂</button>'
       '<button data-btn="APP_SWITCH">▭</button>')

# What the page's button row sends, mapped to Android key names. Keeping the
# mapping here rather than in the page lets both platforms share one page.
KEYS = {
    "BACK": "KEYCODE_BACK",
    "HOME": "KEYCODE_HOME",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
    "BACKSPACE": "KEYCODE_DEL",
    "ENTER": "KEYCODE_ENTER",
}

# Where a package manager drops the jar. Homebrew, the usual Linux prefixes,
# a user-local install, and the two sandboxed Linux formats that put it
# somewhere a prefix search would never reach.
JAR_CANDIDATES = [
    "/opt/homebrew/share/scrcpy/scrcpy-server",
    "/usr/local/share/scrcpy/scrcpy-server",
    "/usr/share/scrcpy/scrcpy-server",
    os.path.expanduser("~/.local/share/scrcpy/scrcpy-server"),
    "/snap/scrcpy/current/usr/local/share/scrcpy/scrcpy-server",
    "/var/lib/flatpak/app/org.openredneck.scrcpy/current/active/files/"
    "share/scrcpy/scrcpy-server",
]


def find_server_jar():
    """Locate scrcpy-server, which is a data file and not on any PATH.

    Looking beside the scrcpy binary comes first and matters most. The
    Windows build is a self-contained folder with the jar sitting next to
    scrcpy.exe, and so is every portable or hand-built copy -- none of which
    any list of distribution prefixes would ever name. Resolving symlinks on
    the way means a /usr/local/bin/scrcpy pointing into a Homebrew cellar or
    a Nix store still finds its own jar.
    """
    env = os.environ.get("SCRCPY_SERVER_PATH")
    if env and os.path.exists(env):
        return env

    beside = []
    exe = shutil.which("scrcpy")
    if exe:
        d = os.path.dirname(os.path.realpath(exe))
        beside = [
            os.path.join(d, "scrcpy-server"),
            os.path.join(d, "scrcpy-server.jar"),
            # The FHS layout: the binary in bin/, its data in ../share/.
            os.path.join(d, os.pardir, "share", "scrcpy", "scrcpy-server"),
        ]

    for cand in beside + JAR_CANDIDATES:
        if cand and os.path.exists(cand):
            return os.path.normpath(cand)
    return None


def install_hint():
    """How to get scrcpy, in the words of the platform we are actually on."""
    if sys.platform == "darwin":
        return "brew install scrcpy"
    if sys.platform.startswith("linux"):
        return ("apt install scrcpy  (or dnf/pacman/snap -- "
                "see github.com/Genymobile/scrcpy)")
    if os.name == "nt":
        return ("winget install Genymobile.scrcpy  (or scoop install scrcpy), "
                "then make sure scrcpy.exe is on PATH")
    return "see github.com/Genymobile/scrcpy"


def scrcpy_version():
    """The jar refuses a client version it does not recognise, so ask scrcpy."""
    out = subprocess.run(["scrcpy", "--version"], capture_output=True,
                         text=True).stdout
    m = re.search(r"scrcpy\s+([0-9][0-9.]*)", out)
    return m.group(1) if m else None


class Shell:
    """One `adb shell` held open, with commands fed to its stdin.

    Every command is followed by a sentinel echo so a caller knows when the
    device has finished rather than merely when the write returned; without it
    a tap's latency is unmeasurable and errors vanish silently.
    """

    def __init__(self, adb):
        self.adb = adb
        self.lock = threading.Lock()
        self.proc = None
        self.seq = 0
        self._open()

    def _open(self):
        self.proc = subprocess.Popen(
            self.adb("shell"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0)
        self.proc.stdin.write(b"echo __up__\n")
        deadline = time.time() + 20
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("adb shell closed during startup")
            if b"__up__" in line:
                return
        raise RuntimeError("adb shell did not come up")

    def run(self, command, timeout=30):
        with self.lock:
            if self.proc.poll() is not None:
                self._open()
            self.seq += 1
            tag = f"__d{self.seq}__"
            self.proc.stdin.write(f"{command}; echo {tag}\n".encode())
            out, deadline = [], time.time() + timeout
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("adb shell closed")
                if tag.encode() in line:
                    return b"".join(out).decode(errors="replace").strip()
                out.append(line)
            raise TimeoutError(command)

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class Control:
    """scrcpy's control socket, for gestures `adb shell input` cannot express.

    `input` has no multi-touch whatsoever, so a pinch has to be built from two
    independently tracked pointers, and only the control protocol offers that.
    It runs as its own server instance (video off) rather than sharing the
    video one, so a viewer connecting or disconnecting never disturbs input.

    One trap, which costs an afternoon if you miss it: the server expects a
    plain ACTION_DOWN and ACTION_UP for *every* finger and works out
    ACTION_POINTER_DOWN and the pointer index from its own pointer table.
    Sending ACTION_POINTER_DOWN (5) directly makes it treat the whole gesture
    as a single long press -- which on a map drops a pin instead of zooming.
    """

    DOWN, UP, MOVE = 0, 1, 2
    INJECT_TOUCH = 2

    def __init__(self, backend):
        self.b = backend
        self.sock = None
        self.proc = None
        self.lock = threading.Lock()
        self.port = random.randint(22000, 22999)
        self.scid = "%08x" % random.getrandbits(31)

    def _connect(self):
        b = self.b
        subprocess.run(b.adb("forward", f"tcp:{self.port}",
                             f"localabstract:scrcpy_{self.scid}"),
                       capture_output=True)
        self.proc = subprocess.Popen(
            b.adb("shell",
                  f"CLASSPATH={REMOTE_JAR} app_process / "
                  f"com.genymobile.scrcpy.Server {b.scrcpy_version} "
                  f"scid={self.scid} tunnel_forward=true raw_stream=true "
                  f"video=false audio=false control=true cleanup=false"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("scrcpy control server exited")
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port),
                                                     timeout=2)
                time.sleep(0.4)     # let the controller finish starting
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("scrcpy control server never accepted a connection")

    def _touch(self, action, pid, x, y):
        w, h = self.b.geometry["width"], self.b.geometry["height"]
        return struct.pack(">BBqiiHHHii", self.INJECT_TOUCH, action, pid,
                           int(max(0, min(w - 1, x))),
                           int(max(0, min(h - 1, y))),
                           w, h, 0xFFFF, 0, 0)

    def _send(self, payload):
        with self.lock:
            if self.sock is None or self.proc is None or self.proc.poll() is not None:
                self.close()
                self._connect()
            try:
                self.sock.sendall(payload)
            except OSError:
                # The server exits when its client goes; rebuild and retry once.
                self.close()
                self._connect()
                self.sock.sendall(payload)

    def pinch(self, cx, cy, scale, ms=400, radius=120, steps=24):
        start, end = radius, max(8.0, radius * scale)
        frames = [self._touch(self.DOWN, 0, cx - start, cy),
                  self._touch(self.DOWN, 1, cx + start, cy)]
        for i in range(1, steps + 1):
            d = start + (end - start) * i / steps
            frames.append(self._touch(self.MOVE, 0, cx - d, cy))
            frames.append(self._touch(self.MOVE, 1, cx + d, cy))
        frames.append(self._touch(self.UP, 1, cx + end, cy))
        frames.append(self._touch(self.UP, 0, cx - end, cy))

        self._send(frames[0] + frames[1])
        time.sleep(0.08)
        pause = max(0.005, (ms / 1000.0) / steps)
        for i in range(2, len(frames) - 2, 2):
            self._send(frames[i] + frames[i + 1])
            time.sleep(pause)
        self._send(frames[-2] + frames[-1])

    def close(self):
        for shut in (lambda: self.sock and self.sock.close(),
                     lambda: self.proc and self.proc.kill()):
            try:
                shut()
            except Exception:
                pass
        self.sock = self.proc = None


class _ScrcpySource:
    """One scrcpy-server instance feeding one viewer.

    The server exits as soon as its client disconnects, so it is started per
    stream rather than kept alive; each gets its own socket id and local port
    so a reconnect never lands on the previous one's leftovers.
    """

    def __init__(self, backend):
        self.b = backend
        self.scid = "%08x" % random.getrandbits(31)
        self.port = random.randint(21000, 21999)
        self.proc = None
        self.sock = None
        self.prime = b""
        self._start()

    def _start(self):
        b = self.b
        subprocess.run(b.adb("forward", f"tcp:{self.port}",
                             f"localabstract:scrcpy_{self.scid}"),
                       capture_output=True)
        self.proc = subprocess.Popen(
            b.adb("shell",
                  f"CLASSPATH={REMOTE_JAR} app_process / "
                  f"com.genymobile.scrcpy.Server {b.scrcpy_version} "
                  f"scid={self.scid} tunnel_forward=true raw_stream=true "
                  f"video=true audio=false control=false "
                  f"video_codec=h264 max_size={b.max_size} "
                  f"video_bit_rate={b.bitrate} max_fps={b.fps} cleanup=false"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # `adb forward` accepts on the local port straight away, before the
        # server has bound its abstract socket on the device, and then drops
        # the connection. A successful connect() therefore proves nothing, and
        # `raw_stream` deliberately omits the dummy byte scrcpy's own client
        # handshakes on -- so the only proof the tunnel is live is real video
        # data. Keep reconnecting until some arrives, and keep the first chunk.
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.close()
                raise RuntimeError("scrcpy server exited during startup")
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
            except OSError:
                time.sleep(0.2)
                continue
            sock.settimeout(2)
            try:
                prime = sock.recv(65536)
            except (socket.timeout, OSError):
                prime = b""
            if prime:
                sock.settimeout(1)
                self.sock, self.prime = sock, prime
                return
            sock.close()
            time.sleep(0.2)
        self.close()
        raise RuntimeError("scrcpy server never sent video")

    def read(self, n):
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b""

    def ended(self):
        return self.proc.poll() is not None

    def close(self):
        for shut in (lambda: self.sock and self.sock.close(),
                     lambda: self.proc and self.proc.kill()):
            try:
                shut()
            except Exception:
                pass
        subprocess.run(self.b.adb("forward", "--remove", f"tcp:{self.port}"),
                       capture_output=True)


class _ScreencapSource:
    """Fallback for viewers without WebCodecs: screencap, as fast as it goes.

    Deliberately crude -- around 8 frames a second -- because it exists only
    for browsers that cannot decode H.264. scrcpy has no MJPEG mode, so there
    is nothing better to fall back to.
    """

    def __init__(self, backend):
        self.b = backend
        self.buf = b""
        self.closed = False

    def read(self, n):
        if self.closed:
            return b""
        if not self.buf:
            shot = subprocess.run(self.b.adb("exec-out", "screencap", "-j"),
                                  capture_output=True, timeout=20).stdout
            if not shot or not shot.startswith(b"\xff\xd8"):
                self.closed = True
                return b""
            self.buf = (b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: %d\r\n\r\n" % len(shot)) + shot + b"\r\n"
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def ended(self):
        return self.closed

    def close(self):
        self.closed = True


class Backend:
    platform = "android"
    mjpeg_content_type = "multipart/x-mixed-replace; boundary=frame"

    def __init__(self, serial=None, fps=30, bitrate="4000000", max_size="1080",
                 adb_bin="adb"):
        self.adb_bin = adb_bin
        self.serial = serial or self._first_device()
        self.fps = fps
        self.bitrate = bitrate
        self.max_size = max_size
        self.nav = NAV

        jar = find_server_jar()
        if not jar:
            raise SystemExit(
                "scrcpy-server not found.\n"
                f"  install scrcpy:  {install_hint()}\n"
                "  or point SCRCPY_SERVER_PATH at the jar directly.")
        self.scrcpy_version = scrcpy_version()
        if not self.scrcpy_version:
            raise SystemExit("could not determine the scrcpy version; is scrcpy on PATH?")
        subprocess.run(self.adb("push", jar, REMOTE_JAR),
                       capture_output=True, check=True)

        self.geometry, self.title = self._describe()
        self.shell = Shell(self.adb)
        # Built on first use: a pinch is rare enough not to pay for a
        # second server process at every start-up.
        self.control = Control(self)

    def _first_device(self):
        out = subprocess.run([self.adb_bin, "devices"], capture_output=True,
                             text=True).stdout
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                return line.split("\t")[0]
        raise SystemExit(
            "no adb device.\n"
            "  emulator -list-avds && emulator -avd <name>   # or plug a phone in")

    def adb(self, *rest):
        return [self.adb_bin, "-s", self.serial] + list(rest)

    def _describe(self):
        def prop(name):
            return subprocess.run(self.adb("shell", "getprop", name),
                                  capture_output=True, text=True).stdout.strip()
        size = subprocess.run(self.adb("shell", "wm", "size"),
                              capture_output=True, text=True).stdout
        m = re.search(r"Override size:\s*(\d+)x(\d+)", size) or \
            re.search(r"(\d+)x(\d+)", size)
        geometry = {"width": int(m.group(1)), "height": int(m.group(2))} if m \
            else {"width": 1080, "height": 1920}
        return geometry, f"{prop('ro.product.model')} (Android {prop('ro.build.version.release')})"

    def open_video(self):
        return _ScrcpySource(self)

    def open_mjpeg(self):
        return _ScreencapSource(self)

    def act(self, name, body):
        if name == "tap":
            ms = body.get("ms")
            if ms and ms >= 500:
                # `input` has no long-press; a swipe that does not move is the
                # documented way to hold.
                self.shell.run(f"input swipe {body['x']} {body['y']} "
                               f"{body['x']} {body['y']} {int(ms)}")
            else:
                self.shell.run(f"input tap {body['x']} {body['y']}")
        elif name == "swipe":
            self.shell.run(f"input swipe {body['x1']} {body['y1']} "
                           f"{body['x2']} {body['y2']} {int(body.get('ms', 250))}")
        elif name == "text":
            # `input text` takes the argument as a shell word and renders %s as
            # a space, so quote it and translate the spaces ourselves.
            text = body["text"].replace("%", "%%").replace(" ", "%s")
            self.shell.run(f"input text {shlex.quote(text)}")
        elif name == "pinch":
            self.control.pinch(body["x"], body["y"], float(body["scale"]),
                               ms=int(body.get("ms", 400)),
                               radius=float(body.get("radius", 120)))
        elif name in ("button", "key"):
            self.shell.run(
                f"input keyevent {KEYS.get(body.get('name'), 'KEYCODE_HOME')}")

    def shutdown(self):
        self.shell.close()
        self.control.close()
