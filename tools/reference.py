#!/usr/bin/env python3
"""Reference canonical vector and wire framing, with the firmware's exact code."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from breaker.canonical import payout, digest, to_hex
from breaker.wire import encode, decode


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("vector")
    enc = sub.add_parser("encode"); enc.add_argument("fields", nargs="+")
    sub.add_parser("decode")
    a = ap.parse_args()
    if a.command == "vector":
        raw = payout(184000)
        print(json.dumps({"canonical": raw.decode(), "hex": to_hex(raw), "sha256": digest(raw)}, indent=2))
    elif a.command == "encode":
        sys.stdout.buffer.write(encode(*a.fields))
    else:
        print(json.dumps(decode(sys.stdin.buffer.readline(1025))))


if __name__ == "__main__": main()
