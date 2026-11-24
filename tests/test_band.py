import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from smtping import Smtping, band  # noqa: E402


class BandTest(unittest.TestCase):
    """The band of the API wins; the status is only the fallback."""

    def client(self, body):
        c = Smtping("k")
        self.addCleanup(c.close)
        c.request = lambda *a, **k: body
        return c

    def test_single(self):
        for given, want in (("judgement", "judgement"), (None, "safe"), ("other", "safe")):
            r = self.client({"email": "a@b.co", "status": "valid", "band": given}).verify("a@b.co")
            self.assertEqual(r["band"], want)

    def test_bulk(self):
        rows = self.client([{"status": "valid", "band": "judgement"}, {"status": "no_reply"}]).bulk.results("j")
        self.assertEqual([r["band"] for r in rows], ["judgement", "avoid"])

    def test_fallback(self):
        self.assertEqual([band(s) for s in ("valid", "no_reply", "catchall")], ["safe", "avoid", "judgement"])


if __name__ == "__main__":
    unittest.main()
