import unittest

from cardiopump_backend.waveform import _flow_to_q15, program_from_text


class WaveformProgramTests(unittest.TestCase):
    def test_resamples_signed_ml_per_s_waveform_to_q15(self):
        program = program_from_text("0 0\n500 1\n1000 0\n", update_hz=4)

        self.assertEqual(program.update_hz, 4)
        self.assertEqual(program.duration_s, 1.0)
        self.assertEqual(program.samples, (0, 16384, 32767, 16384))

    def test_supports_negative_flow(self):
        program = program_from_text("0 0\n500 -1\n1000 0\n", update_hz=4)

        self.assertEqual(program.samples, (0, -16384, -32767, -16384))

    def test_settings_calculate_internal_flow_and_period_scaling(self):
        program = program_from_text("0 0\n500 0.5\n1000 0\n", update_hz=4)
        scaled = program.with_settings(max_flow_ml_s=2.0, period_ms=500.0)

        self.assertEqual(scaled.flow_scale, 4.0)
        self.assertEqual(scaled.period_scale, 0.5)
        self.assertEqual(scaled.max_flow_ml_s, 2.0)
        self.assertEqual(scaled.period_ms, 500.0)
        self.assertEqual(scaled.sample_count, 2)
        self.assertEqual(scaled.samples, (0, 32767))

    def test_reset_scaling_returns_to_source_defaults(self):
        program = program_from_text("0 0\n500 0.5\n1000 0\n", update_hz=4)
        scaled = program.with_settings(max_flow_ml_s=2.0, period_ms=500.0)
        reset = scaled.reset_scaling()

        self.assertEqual(reset.flow_scale, 1.0)
        self.assertEqual(reset.period_scale, 1.0)
        self.assertEqual(reset.max_flow_ml_s, 0.5)
        self.assertEqual(reset.period_ms, 1000.0)

    def test_rounds_non_raster_file_duration_to_nearest_period(self):
        program = program_from_text("0 0\n1001 1\n", update_hz=250)

        self.assertEqual(program.default_period_ms, 1000.0)
        self.assertEqual(program.period_ms, 1000.0)
        self.assertTrue(program.to_json()["default_period_rounded"])

    def test_rejects_non_raster_user_period(self):
        program = program_from_text("0 0\n1000 1\n", update_hz=250)

        with self.assertRaisesRegex(ValueError, "multiple of 4 ms"):
            program.with_settings(max_flow_ml_s=1.0, period_ms=1002.0)

    def test_changing_raster_preserves_a_rounded_period(self):
        program = program_from_text("0 0\n1001 1\n", update_hz=250)
        updated = program.with_update_hz(500)

        self.assertEqual(updated.update_hz, 500)
        self.assertEqual(updated.period_ms, 1000.0)
        self.assertEqual(updated.sample_count, 500)

    def test_negative_flow_clipping_and_total_stroke_volume(self):
        program = program_from_text("0 1\n500 -1\n1000 1\n", update_hz=4)
        clipped = program.with_clip_negative_flow(True)

        self.assertEqual(clipped.samples, (32767, 0, 0, 0))
        self.assertEqual(clipped.negative_clipped_sample_count, 1)
        self.assertAlmostEqual(clipped.total_stroke_volume_ml, 0.25)

    def test_q15_conversion_clips_to_signed_dac_range(self):
        self.assertEqual(_flow_to_q15(1.2), 32767)
        self.assertEqual(_flow_to_q15(-1.2), -32767)

    def test_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "time must be non-negative"):
            program_from_text("-1 0\n1 0\n")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            program_from_text("0 0\n1 1\n1 0\n")

    def test_rejects_too_many_samples(self):
        with self.assertRaisesRegex(ValueError, "limit is 10"):
            program_from_text("0 0\n1000 0\n", update_hz=20, max_samples=10)


if __name__ == "__main__":
    unittest.main()
