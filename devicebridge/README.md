# devicebridge

Watch and drive an **iOS simulator** or an **Android device/emulator** from a
browser, over a single forwarded port.

Built for reaching a development machine's simulators from a phone through an
SSH tunnel, so the picture and the touches both travel over one port you
already forward.

```
┌─────────┐   one forwarded port   ┌──────────────┐   H.264 + input   ┌──────────┐
│ browser │ ─────────────────────▶ │ devicebridge │ ────────────────▶ │  device  │
└─────────┘   H.264 / WebCodecs    └──────────────┘  idb · adb/scrcpy └──────────┘
```

The browser decodes H.264 with WebCodecs, which costs roughly a fiftieth of
MJPEG's bandwidth on a moving screen — **0.4 Mbps against 23 Mbps** measured on
the same simulator. Browsers without `VideoDecoder` fall back to MJPEG
automatically and say so in the status bar.

---

---

## Platform support

The two halves do not travel together. Android needs only `adb` and `scrcpy`,
which run everywhere; an iOS simulator is a macOS framework and cannot exist
anywhere else.

| | macOS | Linux | Windows |
|---|:-:|:-:|:-:|
| **iOS simulator** | ✅ supported | — not possible | — not possible |
| **Android** | ✅ supported | ✅ supported | ⚠️ untested |

`./install.sh` knows this: run it on Linux and it skips the iOS half with a
warning, and `./install.sh ios` on a non-Mac refuses outright rather than
working through a list of prerequisites you could never satisfy.

**On Windows**, the Android target should work and has not been run:

- Use `devicebridge.cmd`, not the bash launcher.
- There is no virtualenv to build — the Android side is standard library
  only, so `devicebridge.cmd` falls back to the system interpreter. You need
  `adb` and `scrcpy` on `PATH`, nothing else.
- `install.sh` itself needs WSL or Git Bash. It isn't required for Android.
- The two things that would have broken it are already handled: process
  groups use `CREATE_NEW_PROCESS_GROUP` and `taskkill /T` where POSIX uses
  `setsid` and `killpg`, and the scrcpy server jar is found beside
  `scrcpy.exe` rather than at a Unix prefix.

If you run it there, the honest status is one line in `../index.json`.

---

## Install

```bash
git clone https://github.com/omnimux-uk/tools
cd tools/devicebridge
./install.sh            # or: ./install.sh ios | ./install.sh android
```

The installer checks each platform's prerequisites, tells you exactly what is
missing, and builds the virtualenv the iOS side needs. It changes nothing
outside this directory — if a tool is missing it prints the `brew` line rather
than running it.

### Prerequisites

| | needs | install |
|---|---|---|
| **iOS** | Xcode command line tools | `xcode-select --install` |
| | `idb_companion` | `brew tap facebook/fb && brew install idb-companion` |
| **Android** | `adb` | `brew install android-platform-tools` |
| | `scrcpy` (for its server jar) | `brew install scrcpy` |
| **both** | Python 3.9–3.13 | `brew install python@3.12` |

Two things that will bite you:

- **Homebrew 7 refuses third-party taps** until trusted. If `brew install
  idb-companion` is rejected, trust the single formula rather than the whole
  tap: `brew trust --formula facebook/fb/idb-companion`.
- **Python must be 3.9–3.13.** `fb-idb` pins old `grpclib`/`protobuf` and will
  not install on newer. The installer searches for a suitable interpreter
  rather than trusting whatever `python3` happens to be — on the machine this
  was written on, `python3` was 3.15 and its venv could not bootstrap pip.
  Override with `PYTHON=/path/to/python3 ./install.sh`.

Android needs no Python packages at all — it is standard library plus `adb`.

---

## Use

Boot a device first, then:

```bash
./devicebridge ios        # http://127.0.0.1:9200   (macOS only)
./devicebridge android    # http://127.0.0.1:9201
```

On Windows that last one is `devicebridge.cmd android`.

Both auto-detect: the iOS side picks the booted simulator and starts
`idb_companion` if it is not already up; the Android side picks the first adb
device and pushes the scrcpy server itself. With more than one device, name it:

```bash
./devicebridge ios --device <UDID>
./devicebridge android --device emulator-5554
```

Then forward the port and open it in a browser:

```bash
ssh -N -L 9200:127.0.0.1:9200 you@the-mac
```

| gesture | effect |
|---|---|
| tap | tap |
| drag | swipe (duration follows the hold) |
| hold ≥ 500 ms | long press |
| ⌂ / ‹ / ▭ | Home, Back, Recents (Back and Recents on Android only) |
| text box → Send | types into the focused field |
| ⌫ / ⏎ | backspace / return |

The status bar shows **H.264** in green or **MJPEG** in amber with the reason,
plus live Mbps and fps.

### Options

```
--port N          default 9200 (ios) / 9201 (android)
--host ADDR       default 127.0.0.1 — read the security note first
--device ID       simulator UDID or adb serial
--fps N           default 30
--keep-companion  iOS: attach to a running idb_companion instead of
                  starting a fresh one (see the ghost-encoder note)
--quality 0..1    iOS encoder quality (default 0.7)
--bitrate BPS     Android (default 4000000)
--max-size PX     Android longest edge (default 1080)
```

---

## Security

**Nothing here is authenticated.** Anyone who can reach the port can watch the
screen, read whatever is on it, and drive the device. Both bridges bind to
`127.0.0.1` for that reason; reach them through an SSH forward.

`--host 0.0.0.0` exists but prints a warning. Don't use it on a network you
don't control.

This is also why the iOS side uses idb rather than Appium + WebDriverAgent:
WebDriverAgent binds ports 8100 and 9100 to *every* interface and offers no way
to change that, so running it means an unauthenticated control API on your LAN
for as long as the session lives.

---

## How it works, and why

| | iOS | Android |
|---|---|---|
| picture | `idb video-stream --format h264` | scrcpy-server, `raw_stream=true` |
| input | idb HID over a held-open gRPC connection | one long-lived `adb shell` |
| pinch | idb's own `HIDPinch` | scrcpy control socket, two pointers |
| coordinates | **points** (e.g. 390×844) | **pixels** (e.g. 1080×2400) |
| fallback | idb MJPEG | `screencap -j`, ~8 fps |

Both produce an H.264 **Annex-B** elementary stream beginning with SPS, PPS and
an IDR, so a viewer joining at any moment can start decoding — and so one page
renders both.

Measured on an iPhone 16e simulator and a Pixel 7 emulator:

| | iOS | Android |
|---|---|---|
| tap latency | **7 ms** | **16 ms** |
| text, 11 chars | 8 ms | 374–806 ms |
| bandwidth, static screen | 0.5 Mbps | 0.15 Mbps |
| glass-to-wire lag | near-instant | ~360 ms |

Alternatives measured and rejected:

- **Appium + WebDriverAgent** for iOS: taps 497 ms against idb's 7 ms, MJPEG
  only at 23 Mbps, and the LAN exposure above. Disabling idle-waiting moved
  497 ms by six.
- **`adb screenrecord`** for Android: produces the same Annex-B and needs
  nothing pushed to the device, but measured **1185 ms** of lag against
  scrcpy's 361 ms.
- **`cmd clipboard` + paste** for fast Android text: not implemented on the
  emulator images tested.

### Multi-touch (disabled in the page)

Pinch is implemented on both backends and reachable at `POST /pinch`, but the
**page no longer emits it**: under real fingers it behaved badly on both
platforms, and a half-recognised two-finger gesture is worse than none. The
page now follows one finger and ignores any other, so a stray second touch
cannot land as a swipe when it lifts.

What is below is still accurate about the backends, and is what any fix would
build on.

iOS gets it free: idb has a first-class `HIDPinch`, so the bridge passes
centre, scale and radius straight through.

Android's `adb shell input` has **no multi-touch at all**, so a pinch has to be
built from two independently tracked pointers over scrcpy's control socket —
a second server instance with `video=false control=true`, held open and used
only for this. One trap worth knowing before you touch that code: the server
expects a plain `ACTION_DOWN`/`ACTION_UP` for *every* finger and derives
`ACTION_POINTER_DOWN` and the pointer index from its own pointer table.
Sending `ACTION_POINTER_DOWN` (5) directly makes it read the whole gesture as
one long press — which on a map drops a pin instead of zooming, and looks
like the coordinates are wrong rather than the protocol.

### The ghost-encoder bug

Essential reading before you change the iOS stream handling.

**`idb_companion` never releases a video encoder when its client goes away.**
Measured, and not on `SIGINT`, not on `SIGTERM`, not on `SIGKILL` — after the
client process is gone the companion carries on encoding, with no process left
to show for it. `pgrep` returns nothing while the work continues.

Those ghosts accumulate. After about four the companion saturates: its log
fills with `N pushes, N overruns`, per-frame encode time climbs from 12 ms to
50 ms, and **every new stream returns zero bytes** until it is restarted. The
symptom shows up as "video just stopped working", typically hours after
whatever actually caused it.

So a producer per viewer cannot work here, however carefully it is shut down.
Instead:

- **`StreamHub`** starts exactly one `idb video-stream` per format for the
  life of the bridge and fans it out to every viewer. Connecting and
  disconnecting costs nothing; a slow viewer has its backlog dropped rather
  than stalling the others.
- **The companion is replaced at startup** (`--keep-companion` to opt out), so
  encoders cannot accumulate across bridge restarts either.
- A viewer attaching mid-stream waits for the next SPS and keyframe. idb emits
  those about every three seconds, and the page cannot decode before a
  keyframe regardless, so nothing is lost.

Verified: thirteen consecutive reconnects, one `idb video-stream` process and
one companion throughout, every connection delivering video.

Android needs none of this — scrcpy's server exits on its own when its client
disconnects, which is why only the iOS side ever collapsed.

---

## Known limits

- **No multi-touch from the page.** Pinch exists on both backends but is not
  emitted by the viewer; see the multi-touch note above. Rotate and
  three-finger gestures were never wired up.
- **Gestures are summarised, not streamed.** The page classifies a touch as
  tap/hold/swipe and posts one request, so there is no live feedback while a
  finger moves. Streaming raw touches would be better and is possible —
  fully on Android, whose control protocol is a touch stream, and for a
  single finger on iOS (verified: repeated HID presses do drag). It is not
  possible for iOS multi-touch at all: `HIDTouch` carries only a point, with
  no finger identifier anywhere in idb's protocol, which is why `HIDPinch`
  has to be a composite primitive.
- **Android text injection is ~50× slower than iOS** (400–800 ms for 11
  characters). `input text` sends one key event per character. The control
  socket is now there for pinch and could carry text too, which would likely
  fix this — not done yet.
- **Android carries ~360 ms of lag** from scrcpy's encoder pipeline. Fine for
  driving an app, not for anything timing-sensitive.
- **WebCodecs needs Safari 16.4+ or Chrome 94+.** Older browsers get the MJPEG
  fallback, which on Android is only ~8 fps.
- **The iOS companion is restarted when the bridge starts.** If you are
  running `idb_companion` for something else, pass `--keep-companion` — and
  read the ghost-encoder note before you do.
- iOS simulators are macOS-only; the Android side works anywhere adb does.

## Layout

```
bridge/
  common.py    HTTP server, stream pumping, process lifecycle
  viewer.py    the page, shared by both platforms
  ios.py       idb backend
  android.py   adb + scrcpy backend
  __main__.py  CLI
install.sh      prerequisite checks and virtualenv (macOS, Linux)
devicebridge    launcher (POSIX)
devicebridge.cmd  launcher (Windows, Android target only)
```

The page is shared deliberately: a fix to the touch mapping or the decoder
should not be able to land on one platform and silently rot on the other.

## Licence

Apache 2.0, with the rest of [omnimux-uk/tools](https://github.com/omnimux-uk/tools).
