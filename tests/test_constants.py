import unittest

from cardiopump_backend.constants import WAVEFORM_UPDATE_RATES


class ConstantsTests(unittest.TestCase):
    def test_only_qualified_waveform_rates_are_advertised(self):
        self.assertEqual(WAVEFORM_UPDATE_RATES, (250, 500))


if __name__ == "__main__":
    unittest.main()
