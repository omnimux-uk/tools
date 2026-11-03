#!/usr/bin/env python3
"""Check index.json against what the app expects of it.

The app reads index.json to decide which tools to offer on the machine it is
talking to, so a mistake here is not a broken build -- it is a tool offered
on a system that cannot run it, failing in front of the user with whatever
error the tool happens to produce. Hence a check rather than a convention.

Run it after editing index.json, and in CI if this repo ever gets one:

    ./check-index.py
"""

import json
import os
import sys

PLATFORMS = {"macos", "linux", "windows"}
STATUSES = {"supported", "untested", "unsupported"}

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    errors = []

    def bad(where, msg):
        errors.append(f"{where}: {msg}")

    with open(os.path.join(HERE, "index.json")) as f:
        doc = json.load(f)

    if doc.get("schema") != 2:
        bad("index.json", f"schema {doc.get('schema')!r}, expected 2")

    seen = set()
    for tool in doc.get("tools", []):
        tid = tool.get("id", "<no id>")
        if tid in seen:
            bad(tid, "duplicate tool id")
        seen.add(tid)

        for field in ("id", "name", "path", "summary", "targets"):
            if not tool.get(field):
                bad(tid, f"missing {field}")

        path = os.path.join(HERE, tool.get("path", ""))
        if not os.path.isdir(path):
            bad(tid, f"path {tool.get('path')!r} is not a directory")
        elif not os.path.isfile(os.path.join(path, "README.md")):
            bad(tid, "no README.md -- every tool documents itself")

        for target in tool.get("targets", []):
            gid = f"{tid}/{target.get('id', '<no id>')}"
            plats = target.get("platforms", {})

            # Every platform must be named explicitly. Silence is the one
            # thing the app cannot act on: it cannot tell "we did not think
            # about Windows" from "Windows does not work", and the two call
            # for different words in front of the user.
            if set(plats) != PLATFORMS:
                bad(gid, f"platforms must name exactly {sorted(PLATFORMS)}, "
                         f"got {sorted(plats)}")
                continue

            for name, info in plats.items():
                status = info.get("status")
                if status not in STATUSES:
                    bad(f"{gid}/{name}", f"status {status!r} not in {sorted(STATUSES)}")
                    continue
                if status == "supported":
                    if name not in target.get("run", {}):
                        bad(f"{gid}/{name}", "supported but no run command")
                elif not info.get("note"):
                    bad(f"{gid}/{name}",
                        f"{status} needs a note saying why -- the app shows it")

            for name in target.get("run", {}):
                if plats.get(name, {}).get("status") == "unsupported":
                    bad(f"{gid}/{name}", "has a run command but is unsupported")

            if not isinstance(target.get("port"), int) and target.get("serves"):
                bad(gid, "serves something but has no port")

    if errors:
        print(f"index.json: {len(errors)} problem(s)\n", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    n = sum(len(t["targets"]) for t in doc["tools"])
    print(f"index.json ok — {len(doc['tools'])} tool(s), {n} target(s)")
    for tool in doc["tools"]:
        for target in tool["targets"]:
            row = "  ".join(
                f"{p}={target['platforms'][p]['status']}"
                for p in ("macos", "linux", "windows"))
            print(f"  {tool['id']}/{target['id']:<10} {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
