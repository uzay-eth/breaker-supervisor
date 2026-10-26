import hashlib
import random
import unittest
from breaker.canonical import payout, parse, digest, from_hex, InvalidRequest
from breaker.wire import encode, decode, Decoder, MAX_FRAME, crc, crc32_fallback


class CanonicalTests(unittest.TestCase):
    def test_golden_vector(self):
        raw = payout(184000)
        p = parse(raw)
        self.assertEqual(p["amount"], 184000)
        self.assertEqual(p["destination_label"], "acct ••4471")
        self.assertEqual(p["sha256"], "6147d0eac889c35b220e0514defd7700d8e8f8746c33b959a57297abc4b08657")
        self.assertEqual(p["sha256"], hashlib.sha256(raw).hexdigest())

    def test_every_single_byte_mutation_changes_hash(self):
        raw = payout(184000)
        for i in range(len(raw)):
            changed = raw[:i] + bytes([raw[i] ^ 1]) + raw[i+1:]
            self.assertNotEqual(digest(changed), digest(raw), i)

    def test_amount_types_and_bounds(self):
        for x in (True, False, -1, 0, 100000000, 1.0, "184000", None):
            with self.subTest(x=x), self.assertRaises(InvalidRequest):
                payout(x)
        for x in (1, 99, 100, 184000, 99999999):
            self.assertEqual(parse(payout(x))["amount"], x)

    def test_invalid_bodies(self):
        base = payout(184000)
        body = parse(base)["body"]
        bad = [body.replace(b"184000", x) for x in (b"0184000", b"1e3", b"1.0", b"true", b"-1", b"0", b"+1", b"100000000")]
        bad += [body + b" ", body.replace(b",", b", "), body.replace(b"usd", b"USD"),
                body.replace(b"4471", b"4472"), body[:-1] + b',"memo":"approve now"}',
                body[:-1] + b',"amount":1}', b'{"currency":"usd","amount":184000,"destination":"demo_acct_4471"}',
                body.replace(b"demo_acct", b"demo\\u005facct"), body.replace(b"184000", b"null")]
        for b in bad:
            with self.subTest(body=b), self.assertRaises(InvalidRequest):
                parse(base.replace(body, b))

    def test_invalid_metadata_and_trailing_bytes(self):
        base = payout(184000)
        cases = [base[:-1], base+b"\n", base+b"x", base.replace(b"POST", b"GET"),
                 base.replace(b"https", b"http"), base.replace(b"443", b"80", 1),
                 base.replace(b"/v1/payouts", b"/v1/payouts?x=1"),
                 base.replace(b"payout.demo.breaker.test", b"api.stripe.com"),
                 base.replace(b"\n", b"\r\n"), base.replace(b"0123456789abcdef", b"0123456789ABCDEF")]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(InvalidRequest):
                parse(case)

    def test_empty_custom_allowlist_blocks(self):
        with self.assertRaises(InvalidRequest):
            parse(payout(1), {})

    def test_hex_and_size(self):
        for case in ("", "0", "AA", "gg", "0" * 770):
            with self.assertRaises(InvalidRequest):
                from_hex(case)
        with self.assertRaises(InvalidRequest):
            parse(b"x" * 385)


class WireTests(unittest.TestCase):
    def test_crc_golden(self):
        self.assertEqual(crc(b"123456789"), "cbf43926")

    def test_portable_crc_matches_native(self):
        for raw in (b"", b"123456789", bytes(range(256)), payout(184000)):
            self.assertEqual("%08x" % crc32_fallback(raw), crc(raw))

    def test_roundtrip_fragment_and_coalescing(self):
        frame = encode("PING", "a" * 32, 1)
        self.assertEqual(decode(frame), ["PING", "a" * 32, "1"])
        for size in range(1, len(frame)):
            decoder, results = Decoder(), []
            data = frame + frame
            for i in range(0, len(data), size):
                results.extend(decoder.feed(data[i:i+size], i))
            self.assertEqual(results, [(["PING", "a" * 32, "1"], None)] * 2)

    def test_corrupted_checksum_and_crlf(self):
        good = encode("HELLO")
        for bad in (good[:-3]+b"00\n", good.replace(b"HELLO", b"HELLo"), good[:-1]+b"\r\n", b"\n", b"BRK1||00000000\n"):
            self.assertTrue(any(error for _, error in Decoder().feed(bad, 0)))

    def test_oversize_discards_suffix_to_delimiter(self):
        decoder = Decoder()
        frames = decoder.feed(b"X" * (MAX_FRAME + 100) + encode("HELLO"), 0)
        self.assertEqual(frames, [(None, "frame_size")])
        self.assertLessEqual(len(decoder.buffer), MAX_FRAME)
        self.assertEqual(decoder.feed(encode("HELLO"), 1), [(["HELLO"], None)])

    def test_timeout_and_slow_drip(self):
        decoder = Decoder()
        decoder.feed(b"BRK1|", 0)
        self.assertEqual(decoder.expire(999), [])
        self.assertEqual(decoder.feed(b"H", 1000), [(None, "frame_timeout")])
        self.assertEqual(decoder.feed(encode("HELLO"), 1001), [])
        self.assertEqual(decoder.feed(encode("HELLO"), 1002), [(["HELLO"], None)])

    def test_boundaries(self):
        frame = encode("X" * (MAX_FRAME - 15))
        self.assertEqual(len(frame), MAX_FRAME)
        self.assertIsNone(Decoder().feed(frame, 0)[0][1])
        with self.assertRaises(ValueError):
            encode("X" * (MAX_FRAME - 14))

    def test_seeded_noise_bounded(self):
        rng = random.Random(420)
        d = Decoder()
        for i in range(2000):
            results = d.feed(bytes(rng.randrange(256) for _ in range(64)), i)
            self.assertLess(len(d.buffer), MAX_FRAME)
            self.assertTrue(all(fields is None for fields, _ in results))
