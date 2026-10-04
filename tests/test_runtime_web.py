import unittest

from config.runtime_web import DEFAULT_DAILY_WEB_PORT, resolve_daily_web_port


class RuntimeWebPortTests(unittest.TestCase):
    def test_unset_uses_normal_daily_port(self):
        self.assertEqual(resolve_daily_web_port({}), DEFAULT_DAILY_WEB_PORT)

    def test_explicit_ephemeral_port_is_used(self):
        self.assertEqual(
            resolve_daily_web_port({"LSA_DAILY_WEB_PORT": "43127"}),
            43127,
        )

    def test_zero_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "outside"):
            resolve_daily_web_port({"LSA_DAILY_WEB_PORT": "0"})

    def test_non_numeric_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            resolve_daily_web_port({"LSA_DAILY_WEB_PORT": "not-a-port"})

    def test_out_of_range_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "outside"):
            resolve_daily_web_port({"LSA_DAILY_WEB_PORT": "65536"})


if __name__ == "__main__":
    unittest.main()
