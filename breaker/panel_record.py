"""Local USB commissioning record; never accepted over the policy UART.

The module has no side effects on import. Unknown/stale/unverified records cannot
select a display. Fingerprints bind the observation to the installed panel code.
"""
import binascii
import hashlib
import json
import os

PROFILES = ("V1_IL3820", "V2_SSD1680")
CHECKS = ["white", "pattern", "inverse", "busy_assert_deassert", "physical_confirm"]
FILES = ("epaper.py", "panel_record.py", "panel_arrival.py", "config.py", "face.py", "font.bin")


def fingerprint(directory=None):
    directory = directory or __file__.rsplit("/", 1)[0]
    h = hashlib.sha256()
    for name in FILES:
        h.update(name.encode() + b"\x00")
        with open(directory + "/" + name, "rb") as source:
            while True:
                data = source.read(256)
                if not data:
                    break
                h.update(data)
        h.update(b"\x00")
    return binascii.hexlify(h.digest()).decode()


def valid(record, expected_fingerprint):
    if (type(expected_fingerprint) is not str or len(expected_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in expected_fingerprint)):
        return False
    if type(record) is not dict or set(record) != {"schema", "profile", "identity", "checks", "fingerprint", "verified"}:
        return False
    identity = record["identity"]
    return (type(record["schema"]) is int and record["schema"] == 1
            and record["verified"] is True and record["profile"] in PROFILES
            and record["checks"] == CHECKS and record["fingerprint"] == expected_fingerprint
            and type(identity) is str and 1 <= len(identity) <= 160
            and all(32 <= ord(c) <= 126 for c in identity))


def load(path, expected_fingerprint=None):
    try:
        with open(path, "r") as source:
            data = source.read(1025)
        if len(data) > 1024:
            return None
        record = json.loads(data)
        if valid(record, expected_fingerprint or fingerprint()):
            return record
    except (OSError, ValueError, TypeError):
        pass
    return None


def invalidate(path):
    try:
        os.remove(path)
    except OSError:
        # Absence is safe, but permission/media failures must not preserve an
        # old qualification silently. Verify absence before the test proceeds.
        try:
            with open(path, "r"):
                pass
        except OSError as error:
            if error.args and error.args[0] == 2:
                return
            raise
        raise OSError("cannot invalidate old panel record")


def save(path, profile, identity, tested_fingerprint):
    record = {"schema": 1, "profile": profile, "identity": identity,
              "checks": CHECKS, "fingerprint": tested_fingerprint, "verified": True}
    if not valid(record, tested_fingerprint):
        raise ValueError("invalid panel qualification")
    temporary = path + ".tmp"
    with open(temporary, "w") as target:
        target.write(json.dumps(record))
    os.rename(temporary, path)
    return record
