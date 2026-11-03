# tools

Tools [Omnimux](https://omnimux.uk) installs and runs on your machines over SSH.

Each one also stands on its own: clone the repo, run the tool's `install.sh`,
and you never have to touch the app. Nothing here phones home, and nothing
installs itself outside its own directory.

## What's here

| tool | what it does | platforms |
|---|---|---|
| [**devicebridge**](devicebridge/) | Watch and drive an iOS simulator or Android device from a browser, over one forwarded port | macOS, Linux |

`index.json` at the root is the machine-readable version of that table — the
app reads it to know what it can offer, so a new tool appears in the app
without a new app build.

## Using one by hand

```bash
git clone https://github.com/omnimux-uk/tools
cd tools/devicebridge
./install.sh
```

Each tool's own README has its prerequisites, options and limits. The
installers check what's missing and print the `brew`/`apt` line rather than
running it, so you can see what they'd do before anything happens.

## A word about what these are

These run on a development machine and several of them open a local port.
**None of them authenticate.** They bind to `127.0.0.1` and expect you to
reach them through an SSH forward — which is how Omnimux connects to a machine
in the first place:

```bash
ssh -N -L 9200:127.0.0.1:9200 you@your-machine
```

Anything that can be bound to a routable address says so, warns when you do it,
and makes you pass a flag. Read the tool's Security section before you reach
for that flag.

## Adding a tool

One directory at the top level, holding:

- `README.md` — what it is, prerequisites, how to run it, what it can't do
- `install.sh` — checks prerequisites, changes nothing outside the directory,
  prints install lines rather than running them
- an executable launcher named after the tool
- an entry in `index.json`

Keep comments explaining *why*, in prose. Several of the sharpest ones here
record a constraint that cost a day to find — an encoder that never releases,
a protocol that lies about its own field. Those are the comments worth having.

## Licence

Apache 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
