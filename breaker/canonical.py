"""Exact-byte, deliberately small payout grammar, shared with CM5 and Pico.

No JSON library normalization. Strictly parse a fixed record, then require
byte-for-byte reconstruction. No floats, escapes, duplicate keys or free text.
"""
import binascii
import hashlib

MAX_CANONICAL = 384
MAX_AMOUNT = 99999999
DEFAULT_ALLOWLIST = {"demo_acct_4471": "acct ••4471"}  # Device-owned label; never supplied over UART.
PREFIX = (b"BREAKER-PAYOUT-1\nPOST\nhttps\npayout.demo.breaker.test\n443\n"
          b"/v1/payouts\napplication/json\n")
HEX = "0123456789abcdef"


class InvalidRequest(ValueError):
    pass


def is_hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in HEX for c in value)


def uint(value, maximum=2147483647, minimum=1):
    if (not isinstance(value, str) or not value or len(value) > 10
            or any(c not in "0123456789" for c in value)
            or (len(value) > 1 and value[0] == "0")):
        raise InvalidRequest("integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise InvalidRequest("integer_range")
    return number


def digest(raw):
    return binascii.hexlify(hashlib.sha256(raw).digest()).decode("ascii")


def from_hex(value):
    if not isinstance(value, str) or not value or len(value) % 2 or len(value) > MAX_CANONICAL * 2:
        raise InvalidRequest("canonical_size")
    if not all(c in HEX for c in value):
        raise InvalidRequest("canonical_hex")
    return binascii.unhexlify(value)


def to_hex(raw):
    return binascii.hexlify(raw).decode("ascii")


def payout(amount, destination="demo_acct_4471", idempotency_key="0123456789abcdef0123456789abcdef",
           allowlist=None):
    """Reference constructor. Amount is integer cents (bool is NOT an int here)."""
    if type(amount) is not int or not 1 <= amount <= MAX_AMOUNT:
        raise InvalidRequest("amount")
    allowed = DEFAULT_ALLOWLIST if allowlist is None else allowlist
    if destination not in allowed or not destination.startswith("demo_acct_"):
        raise InvalidRequest("destination")
    if len(destination) > 32 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in destination):
        raise InvalidRequest("destination")
    if not is_hex(idempotency_key, 32):
        raise InvalidRequest("idempotency")
    body = ('{"amount":%d,"currency":"usd","destination":"%s"}' % (amount, destination)).encode("ascii")
    return PREFIX + idempotency_key.encode("ascii") + b"\n" + body + b"\n"


def parse(raw, allowlist=None):
    """Returns only fields parsed by this supervisor; never trusts a supplied label."""
    allowed = DEFAULT_ALLOWLIST if allowlist is None else allowlist
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_CANONICAL:
        raise InvalidRequest("canonical_size")
    if any(c < 32 and c != 10 or c > 126 for c in raw):
        raise InvalidRequest("ascii")
    lines = raw.split(b"\n")
    if len(lines) != 10 or lines[-1] != b"" or not raw.startswith(PREFIX):
        raise InvalidRequest("record")
    key = lines[7].decode("ascii")
    body = lines[8]
    fields = body.split(b",")
    if (len(fields) != 3 or not fields[0].startswith(b'{"amount":')
            or fields[1] != b'"currency":"usd"'
            or not fields[2].startswith(b'"destination":"')
            or not fields[2].endswith(b'"}')):
        raise InvalidRequest("body")
    amount = uint(fields[0][10:].decode("ascii"), MAX_AMOUNT)
    destination = fields[2][15:-2].decode("ascii")
    if raw != payout(amount, destination, key, allowed):
        raise InvalidRequest("noncanonical")
    return {"amount": amount, "currency": "usd", "destination": destination,
            "destination_label": allowed[destination], "idempotency_key": key,
            "method": "POST", "host": "payout.demo.breaker.test", "port": 443,
            "scheme": "https", "path": "/v1/payouts", "content_type": "application/json",
            "body": body, "sha256": digest(raw), "canonical": raw}
