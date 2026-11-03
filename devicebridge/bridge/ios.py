"""iOS simulator backend: idb for both the picture and the touches.

idb_companion exposes the CoreSimulator framebuffer as an H.264 Annex-B stream
and injects HID events over gRPC. Both go through its unix socket, so nothing
listens on a routable address -- unlike the WebDriverAgent route, whose 8100
and 9100 are bound to every interface and cannot be told otherwise.

Measured against WebDriverAgent on the same simulator: taps 7 ms rather than
497 ms (a held-open gRPC connection against W3C actions plus process startup),
and 0.4 Mbps rather than 23 Mbps on a moving screen, because H.264 sends what
changed while MJPEG resends whole frames.
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import subprocess
import threading
import time

from .common import StreamHub, kill_group, spawn_kwargs

LOG = logging.getLogger("devicebridge.ios")

NAV = '<button data-btn="HOME">⌂</button>'
# The shared page names its keys; idb wants HID usage codes.
KEYS = {"BACKSPACE": 42, "ENTER": 40}


def idb_cli():
    """Path to the `idb` command.

    fb-idb installs it into the same virtualenv as the library we import, and
    that bin directory is not on PATH when the bridge is launched by its own
    interpreter -- so look beside the running python before trusting PATH.
    """
    beside = os.path.join(os.path.dirname(sys.executable), "idb")
    if os.path.exists(beside):
        return beside
    return shutil.which("idb") or "idb"


def booted_udid():
    out = subprocess.run(["xcrun", "simctl", "list", "devices", "booted", "-j"],
                         capture_output=True, text=True).stdout
    for devices in json.loads(out or "{}").get("devices", {}).values():
        for d in devices:
            if d.get("state") == "Booted":
                return d["udid"]
    return None


def ensure_companion(udid, wait=30, fresh=True):
    """Bring up idb_companion for this simulator, by default a brand new one.

    `fresh` exists because the companion never releases a video encoder (see
    StreamHub), so encoders accumulate across bridge restarts even though each
    run only ever starts one. Replacing the companion at startup guarantees a
    clean slate, and it costs about two seconds. Pass fresh=False to attach to
    a companion someone else is using -- at the price of inheriting whatever
    state it is already in.
    """
    sock = f"/tmp/idb/{udid}_companion.sock"
    if os.path.exists(sock):
        if not fresh:
            return None, sock
        subprocess.run(["pkill", "-f", f"idb_companion.*{udid}"],
                       capture_output=True)
        time.sleep(1.5)
        try:
            os.unlink(sock)
        except OSError:
            pass
    os.makedirs("/tmp/idb", exist_ok=True)
    proc = subprocess.Popen(
        ["idb_companion", "--udid", udid, "--grpc-domain-sock", sock],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **spawn_kwargs())
    deadline = time.time() + wait
    while time.time() < deadline:
        if os.path.exists(sock):
            return proc, sock
        if proc.poll() is not None:
            raise RuntimeError("idb_companion exited during startup")
        time.sleep(0.3)
    raise RuntimeError(f"idb_companion did not create {sock}")


class _Client:
    """A persistent idb connection usable from the server's worker threads.

    idb's client is asyncio-only, so one event loop lives in a thread of its
    own and the HTTP handlers hand coroutines to it. The connection is rebuilt
    on failure because the companion dies with the simulator.
    """

    def __init__(self, sock):
        from idb.common.types import DomainSocketAddress  # noqa: F401
        self.sock = sock
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.up = threading.Event()
        self.describe = None
        threading.Thread(target=self._thread, daemon=True).start()
        self.up.wait(timeout=30)

    def _thread(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._hold())

    async def _hold(self):
        from idb.common.types import DomainSocketAddress
        from idb.grpc.client import Client
        while True:
            try:
                async with Client.build(
                    address=DomainSocketAddress(path=self.sock), logger=LOG
                ) as client:
                    self.client = client
                    self.describe = await client.describe()
                    self.up.set()
                    # Nothing to drive from here; the connection only has to
                    # stay open for the handler threads to borrow.
                    await asyncio.Event().wait()
            except Exception as e:
                LOG.warning("idb connection lost: %s", e)
                self.client = None
                self.up.clear()
                await asyncio.sleep(1)

    def call(self, make_coro, timeout=30):
        if not self.up.wait(timeout=10) or self.client is None:
            raise RuntimeError("idb companion unavailable")
        return asyncio.run_coroutine_threadsafe(
            make_coro(self.client), self.loop).result(timeout=timeout)


class Backend:
    platform = "ios"
    mjpeg_content_type = "multipart/x-mixed-replace; boundary=idbframe"

    def __init__(self, udid=None, fps=30, quality="0.7", idb_bin=None,
                 fresh_companion=True):
        self.udid = udid or booted_udid()
        if not self.udid:
            raise SystemExit(
                "no booted simulator.\n"
                "  open -a Simulator            # then boot a device, or\n"
                "  xcrun simctl boot <udid>")
        self.fps = fps
        self.quality = quality
        self.idb_bin = idb_bin or idb_cli()
        self.companion, sock = ensure_companion(self.udid, fresh=fresh_companion)
        self.client = _Client(sock)
        if not self.client.up.is_set():
            raise SystemExit(f"could not reach idb companion on {sock}")

        dims = getattr(self.client.describe, "screen_dimensions", None)
        # idb's input coordinates are points, not pixels -- the opposite of
        # Android, where `input` wants the physical resolution.
        self.geometry = {"width": dims.width_points, "height": dims.height_points,
                         "pixelWidth": dims.width, "pixelHeight": dims.height,
                         "density": dims.density}
        self.title = self.client.describe.name
        self.nav = NAV

        # Exactly one encoder per format, for the life of the bridge. See
        # StreamHub for why starting one per viewer cannot work here.
        self._hubs = {}
        self._lock = threading.Lock()

    def _video(self, fmt):
        with self._lock:
            hub = self._hubs.get(fmt)
            if hub is None:
                hub = self._hubs[fmt] = StreamHub(
                    [self.idb_bin, "video-stream", "--udid", self.udid,
                     "--format", fmt, "--fps", str(self.fps),
                     "--compression-quality", self.quality])
        return hub.subscribe()

    def open_video(self):
        return self._video("h264")

    def open_mjpeg(self):
        return self._video("mjpeg")

    def act(self, name, body):
        from idb.common.types import HIDButtonType
        c = self.client
        if name == "tap":
            ms = body.get("ms")
            c.call(lambda k: k.tap(body["x"], body["y"],
                                   (ms / 1000.0) if ms else None))
        elif name == "swipe":
            c.call(lambda k: k.swipe((body["x1"], body["y1"]),
                                     (body["x2"], body["y2"]),
                                     body.get("ms", 250) / 1000.0))
        elif name == "text":
            c.call(lambda k: k.text(body["text"]))
        elif name == "pinch":
            # idb has a first-class pinch; no need to synthesise two pointers.
            c.call(lambda k: k.pinch(body["x"], body["y"], float(body["scale"]),
                                     duration=body.get("ms", 400) / 1000.0,
                                     radius=float(body.get("radius", 120))))
        elif name == "button":
            c.call(lambda k: k.button(HIDButtonType[body.get("name", "HOME")]))
        elif name == "key":
            c.call(lambda k: k.key(KEYS.get(body.get("name"), 42)))

    def shutdown(self):
        for hub in self._hubs.values():
            hub.stop()
        if self.companion:
            kill_group(self.companion)
