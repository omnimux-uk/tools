"""Pieces both bridges need: the HTTP shell and stream plumbing.

The two platforms differ only in where the pictures and the touches go. Keeping
the server, the stream framing and the action dispatch here means a fix lands
on both at once rather than on whichever one was being worked on that day.
"""

import json
import os
import queue
import select
import subprocess
import threading
import signal
import time

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# If a producer stops producing, a blocking read would hold the stream open
# forever and never notice the viewer had gone. That is not hypothetical: it
# left orphaned encoder processes running against the iOS simulator until the
# companion was saturated and every new stream returned zero bytes.
STREAM_IDLE_TIMEOUT = 10.0


POSIX = os.name == "posix"


def spawn_kwargs():
    """Popen arguments that put a child in a group of its own.

    These video producers spawn workers, and killing only the process we
    started leaves those running and still writing -- see kill_group. Putting
    the child at the head of its own group is what makes the whole tree
    reachable, and it is spelled differently on each platform: setsid on
    POSIX, CREATE_NEW_PROCESS_GROUP on Windows. Passing preexec_fn on Windows
    does not degrade gracefully, it raises.
    """
    if POSIX:
        return {"preexec_fn": os.setsid}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def kill_group(proc, grace=3.0):
    """Stop a child and everything it spawned, politely first.

    Two things learned the hard way, both of which look like "the stream just
    stopped working" hours later:

    The process group matters -- a bare kill on these video producers leaves
    the real worker running and still writing.

    And the signal matters even more. SIGKILL gives the client no chance to
    close its gRPC stream, so idb_companion carries on encoding for a viewer
    that no longer exists. Those ghost encoders accumulate until the companion
    is saturated ("N pushes, N overruns" in its log) and every new stream
    returns zero bytes, curable only by restarting it. SIGINT lets the client
    shut the stream down; SIGKILL is the fallback if it will not go.
    """
    if proc is None:
        return
    if not POSIX:
        # Windows has neither process groups in the POSIX sense nor SIGKILL.
        # CTRL_BREAK_EVENT is the one signal that reaches a whole group, and
        # only a group made with CREATE_NEW_PROCESS_GROUP -- which is what
        # spawn_kwargs() asks for. It plays the part SIGINT plays below: the
        # child gets to close its stream before anything harsher. taskkill /T
        # is the fallback, and unlike proc.kill() it takes the children too.
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=grace)
            return
        except Exception:
            pass
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except Exception:
            return
        try:
            proc.wait(timeout=grace if sig is signal.SIGINT else 1.0)
            return
        except Exception:
            continue


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP surface shared by both bridges.

    Subclasses provide `backend`, a platform object exposing:
        geometry        dict with width/height in the device's input space
        title           what to call the device in the page title
        nav             HTML for the platform's navigation buttons
        fps             frame rate the page assumes for timestamps
        open_video()    -> an object with .read(n) and .close()
        open_mjpeg()    -> the same, or None if unsupported
        act(name, body) -> perform an action, raising on failure
    """

    protocol_version = "HTTP/1.1"
    backend = None

    def log_message(self, *a):
        pass    # the terminal is for the operator, not for every frame

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from . import viewer
        path = self.path.split("?")[0]
        b = self.backend
        if path == "/":
            g = b.geometry
            body = viewer.render(b.title, g["width"], g["height"], b.fps, b.nav)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/meta":
            self._json(dict(b.geometry, name=b.title))
        elif path == "/h264":
            self._pump(b.open_video, "video/h264")
        elif path == "/stream":
            if b.open_mjpeg is None:
                self._json({"error": "no MJPEG fallback on this platform"}, 404)
            else:
                self._pump(b.open_mjpeg, b.mjpeg_content_type)
        elif path == "/health":
            self._json({"ok": True, "platform": b.platform, "device": b.title})
        else:
            self.send_error(404)

    def _pump(self, open_source, ctype):
        try:
            source = open_source()
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 502)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        # An endless stream has no length, and chunked framing would corrupt
        # both an Annex-B stream and MJPEG, so close-delimited HTTP/1.0 it is.
        self.protocol_version = "HTTP/1.0"
        self.send_header("Connection", "close")
        self.end_headers()
        idle_since = time.monotonic()
        try:
            prime = getattr(source, "prime", b"")
            if prime:
                self.wfile.write(prime)
            while True:
                chunk = source.read(32768)
                if chunk:
                    self.wfile.write(chunk)
                    idle_since = time.monotonic()
                elif source.ended():
                    break
                elif time.monotonic() - idle_since > STREAM_IDLE_TIMEOUT:
                    break   # producing nothing; do not hold it open
        except Exception:
            pass    # viewer went away, or the forward dropped
        finally:
            source.close()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"error": "bad json"}, 400)
            return
        action = self.path.split("?")[0].lstrip("/")
        if action not in ("tap", "swipe", "pinch", "text", "button", "key"):
            self._json({"error": "unknown action"}, 404)
            return
        try:
            self.backend.act(action, body)
            self._json({"ok": True})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 502)


class StreamHub:
    """One producer process, fanned out to however many viewers attach.

    This exists because idb_companion never releases a video encoder when its
    client goes away -- measured, and not on SIGINT, SIGTERM or SIGKILL. Every
    stream ever started therefore keeps encoding forever; after about four the
    companion saturates (its log fills with `N pushes, N overruns`, per-frame
    encode time climbs from 12 ms to 50 ms) and every new stream returns zero
    bytes until it is restarted.

    Starting a producer per viewer is consequently unworkable. One producer per
    format is started on first use and kept for the life of the bridge, and
    viewers share it. A viewer joining mid-stream sees nothing until the next
    SPS and keyframe arrive -- about three seconds with idb's defaults -- which
    the page already handles, since it cannot decode before a keyframe anyway.
    """

    def __init__(self, argv):
        self.argv = argv
        self.proc = None
        self.subs = []
        self.lock = threading.Lock()

    def _ensure(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            self.proc = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                **spawn_kwargs())
            threading.Thread(target=self._reader, args=(self.proc,),
                             daemon=True).start()

    def _reader(self, proc):
        while True:
            chunk = proc.stdout.read(32768)
            if not chunk:
                return
            with self.lock:
                subs = list(self.subs)
            for q in subs:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    # One viewer that cannot keep up must not stall the rest;
                    # drop its oldest frame data rather than block the reader.
                    try:
                        q.get_nowait()
                        q.put_nowait(chunk)
                    except (queue.Empty, queue.Full):
                        pass

    def subscribe(self):
        self._ensure()
        q = queue.Queue(maxsize=256)
        with self.lock:
            self.subs.append(q)
        return HubSource(self, q)

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def stop(self):
        kill_group(self.proc)


class HubSource:
    """One viewer's view of a StreamHub. Closing it detaches, nothing more."""

    prime = b""

    def __init__(self, hub, q):
        self.hub = hub
        self.q = q
        self.closed = False

    def read(self, n):
        try:
            return self.q.get(timeout=1.0)
        except queue.Empty:
            return b""

    def ended(self):
        return self.closed or (self.hub.proc is not None
                               and self.hub.proc.poll() is not None)

    def close(self):
        self.closed = True
        self.hub.unsubscribe(self.q)


class PipeSource:
    """A subprocess's stdout, read without ever blocking indefinitely."""

    def __init__(self, proc, prime=b""):
        self.proc = proc
        self.prime = prime
        os.set_blocking(proc.stdout.fileno(), False)

    def read(self, n):
        ready, _, _ = select.select([self.proc.stdout], [], [], 1.0)
        if not ready:
            return b""
        return self.proc.stdout.read(n) or b""

    def ended(self):
        return self.proc.poll() is not None

    def close(self):
        kill_group(self.proc)


def serve(backend, host, port):
    handler = type("Handler", (BridgeHandler,), {"backend": backend})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    g = backend.geometry
    print(f"device: {backend.title}  {g['width']}x{g['height']}", flush=True)
    print(f"bridge on http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        backend.shutdown()
