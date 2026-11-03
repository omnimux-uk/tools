"""devicebridge entry point: `python -m bridge ios|android [options]`."""

import argparse
import sys

from .common import serve


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="devicebridge",
        description="Watch and drive an iOS simulator or Android device "
                    "from a browser, over one forwarded port.")
    p.add_argument("platform", choices=("ios", "android"))
    p.add_argument("--port", type=int,
                   help="default 9200 for ios, 9201 for android")
    p.add_argument("--host", default="127.0.0.1",
                   help="default 127.0.0.1; see the README before changing it")
    p.add_argument("--device",
                   help="simulator UDID or adb serial (default: the only one booted)")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--keep-companion", action="store_true",
                   help="iOS only: attach to a running idb_companion instead "
                        "of starting a fresh one")
    p.add_argument("--quality", default="0.7",
                   help="iOS only: JPEG/encoder quality, 0 to 1")
    p.add_argument("--bitrate", default="4000000", help="Android only")
    p.add_argument("--max-size", default="1080",
                   help="Android only: longest edge in pixels")
    args = p.parse_args(argv)

    if args.host != "127.0.0.1":
        # Neither stream nor the action endpoint has any authentication: anyone
        # who can reach the port can watch the screen and drive the device.
        print("warning: binding off loopback exposes the device to your whole "
              "network, unauthenticated. Prefer an SSH forward.", file=sys.stderr)

    if args.platform == "ios":
        from .ios import Backend
        backend = Backend(udid=args.device, fps=args.fps, quality=args.quality,
                          fresh_companion=not args.keep_companion)
        port = args.port or 9200
    else:
        from .android import Backend
        backend = Backend(serial=args.device, fps=args.fps,
                          bitrate=args.bitrate, max_size=args.max_size)
        port = args.port or 9201

    serve(backend, args.host, port)


if __name__ == "__main__":
    main()
