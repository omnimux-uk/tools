# tools

Tools [Omnimux](https://omnimux.uk) installs and runs on your machines over SSH.

Each one also stands on its own: clone the repo, run the tool's installer, and
you never have to touch the app. Nothing here phones home, and nothing
installs itself outside its own directory.

## What's here

| tool | what it does | macOS | Linux | Windows |
|---|---|:-:|:-:|:-:|
| [**devicebridge**](devicebridge/) — iOS simulator | Stream and drive the booted simulator in a browser | ✅ | — | — |
| [**devicebridge**](devicebridge/) — Android | Stream and drive any adb device or emulator | ✅ | ✅ | ⚠️ |

✅ supported  ⚠️ untested  — not possible

## Targeting

A tool is not simply "for macOS" or "for Linux". `devicebridge` is one program
with two targets, and they differ: Android works wherever `adb` and `scrcpy`
do, while an iOS simulator is a macOS framework and no amount of effort makes
it exist elsewhere.

So support is declared **per target, per platform**, in `index.json`:

```json
{
  "id": "android",
  "platforms": {
    "macos":   { "status": "supported" },
    "linux":   { "status": "supported" },
    "windows": { "status": "untested", "note": "why, in one sentence" }
  },
  "run": { "linux": "./devicebridge android", "windows": "devicebridge.cmd android" },
  "port": 9201
}
```

Three statuses, and the distinction between the last two is the point:

| status | means | the app should |
|---|---|---|
| `supported` | run and verified here | offer it |
| `untested` | the code paths exist, nobody has run them on that OS | offer it with the note attached |
| `unsupported` | cannot work there, ever or for now | hide it, and say why if asked |

**Every target names all three platforms explicitly.** Omitting one is not
allowed, because silence is the one thing the app can't act on: it can't tell
"nobody thought about Windows" from "Windows can't do this", and those deserve
different words in front of a user. Anything not `supported` must carry a
`note` — that string is what gets shown, so write it for a reader.

`run` is keyed by platform too, since the entry point isn't always the same
file: POSIX gets `./devicebridge`, Windows gets `devicebridge.cmd`.

Check it after editing:

```bash
./check-index.py
```

That enforces the rules above. A mistake in `index.json` isn't a broken
build — it's a tool offered on a machine that can't run it, failing in front
of a user with whatever error the tool happens to produce.

## Using one by hand

```bash
git clone https://github.com/omnimux-uk/tools
cd tools/devicebridge
./install.sh            # or install.sh android, if that's all you want
```

Each tool's README has its prerequisites, options and limits. The installers
check what's missing and print the install line for *your* platform rather
than running it, so you can see what they'd do before anything happens.

## A word about what these are

These run on a development machine and several of them open a local port.
**None of them authenticate.** They bind to `127.0.0.1` and expect you to
reach them through an SSH forward — which is how Omnimux connects to a
machine in the first place:

```bash
ssh -N -L 9200:127.0.0.1:9200 you@your-machine
```

Anything that can be bound to a routable address says so, warns when you do
it, and makes you pass a flag. Read the tool's Security section before you
reach for that flag.

## Adding a tool

One directory at the top level, holding:

- `README.md` — what it is, prerequisites, how to run it, what it can't do
- an installer that checks prerequisites, changes nothing outside the
  directory, and prints install lines rather than running them
- an executable launcher named after the tool
- an entry in `index.json` with a target per thing it can drive, each naming
  all three platforms — then `./check-index.py`

Keep comments explaining *why*, in prose. Several of the sharpest ones here
record a constraint that cost a day to find: an encoder that never releases,
a protocol that lies about its own field, a shell builtin that turns a
skipped step into a failed script. Those are the comments worth having.

## Licence

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
