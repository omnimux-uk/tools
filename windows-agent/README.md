# windows-agent

`omnimux-agent` — the Windows counterpart of the machine-side toolkit. One
static Go binary that publishes notifications to ntfy and runs the reply
daemon against [psmux](https://github.com/jart/psmux), where Linux and macOS
use `tmux-notify` and `tmuxremote-reply-daemon`.

Normally you never install this by hand: the Omnimux app uploads it over SFTP
during **Set up notifications** and runs `install.ps1` for you. What follows
is for doing it yourself, or for checking what the app installed.

## Why it lives here and not in the app

It used to ship inside the app bundle, where it was **7.1 MB of 9.3 MB of
assets** — carried by every build, including iOS and Android builds that can
never execute a Windows binary. Only the amd64 copy fitted, so the app
refused Windows on ARM outright.

As a release asset it costs nothing to carry, both architectures are
available, and the app ships a few hundred bytes of SHA-256 digests instead.
That keeps the guarantee the bundle used to provide: the app vouches for
exactly the bytes it was built against, because the digest is signed inside
the bundle, and a download that does not match it is refused.

## Install by hand

Download from the [latest release](https://github.com/omnimux-uk/tools/releases),
picking the architecture that matches the machine:

| Windows on | file |
|---|---|
| Intel / AMD 64-bit | `omnimux-agent-windows-amd64.exe` |
| ARM64 | `omnimux-agent-windows-arm64.exe` |

**Verify it before running it.** In PowerShell:

```powershell
(Get-FileHash .\omnimux-agent-windows-amd64.exe -Algorithm SHA256).Hash.ToLower()
```

and compare against `SHA256SUMS` from the same release. Then:

```powershell
Rename-Item omnimux-agent-windows-amd64.exe omnimux-agent.exe
.\install.ps1
```

`install.ps1` puts the agent in `%LOCALAPPDATA%\Omnimux`, writes its config to
`%APPDATA%\tmuxremote`, registers a logon Scheduled Task for the reply daemon,
and can add Claude Code and Codex hooks. `.\install.ps1 -Uninstall` reverses
all of it.

Questions need `psmux` to find the pane that asked them:

```powershell
winget install psmux
```

Without it notifications still publish; only Yes/No replies need it.

## Where the binaries come from

Built from `server/agent/` in the Omnimux source tree, which stays the
canonical home — the Go code and `install.ps1` are a matched pair and should
not be separated:

```bash
scripts/build-agent.sh      # both architectures, plus SHA256SUMS and a manifest
```

That script prints the `gh release create` line that publishes them here. The
release is the distribution point; it is not a second copy of the source.

Builds are reproducible on a given Go toolchain: two runs of the same source
produce byte-identical binaries, so the digests can be checked independently.

## Licence

Apache 2.0, with the rest of [omnimux-uk/tools](https://github.com/omnimux-uk/tools).
