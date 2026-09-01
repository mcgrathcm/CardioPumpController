import unittest

from cardiopump_backend.waveform import (
    _control_point_slopes,
    _flow_to_q15,
    constant_control_point_program,
    curve_designer_checkpoint_from_text,
    curve_designer_checkpoint_text,
    curve_designer_csv_text,
    control_point_program_from_text,
    insert_control_point,
    move_control_point,
    program_from_text,
    program_from_control_points,
    remove_control_point,
    round_to_raster_ms,
    WaveformPoint,
)


class WaveformProgramTests(unittest.TestCase):
    def test_resamples_signed_ml_per_s_waveform_to_q15(self):
        program = program_from_text("0 0\n500 1\n1000 0\n", update_hz=4)

        self.assertEqual(program.update_hz, 4)
        self.assertEqual(program.duration_s, 1.0)
        self.assertEqual(program.samples, (0, 16384, 32767, 16384))

    def test_accepts_comma_delimited_waveform(self):
        program = program_from_text("0,0\n500,1\n1000,0\n", update_hz=4)

        self.assertEqual(program.samples, (0, 16384, 32767, 16384))

    def test_applies_scale_metadata_to_normalized_waveform(self):
        program = program_from_text(
            "# Scale = 135.0;\n"
            "# Columns: time_ms,flow_normalized\n"
            "0,0\n455,1\n910,0\n",
            update_hz=250,
        )

        self.assertEqual(program.flow_scale, 135.0)
        self.assertEqual(program.max_flow_ml_s, 135.0)
        self.assertEqual(program.period_ms, 912.0)

    def test_curve_fit_can_inherit_source_scale_and_period(self):
        fit = control_point_program_from_text(
            "# Scale = 135.0;\n"
            "# Columns: time_ms,flow_normalized\n"
            "0,0\n455,1\n910,0\n",
            3,
            update_hz=250,
        )

        self.assertEqual(fit.program.max_flow_ml_s, 135.0)
        self.assertEqual(fit.program.period_ms, 912.0)

    def test_curve_designer_checkpoint_round_trips_editable_state(self):
        program = program_from_control_points(
            (
                WaveformPoint(0.0, 0.2),
                WaveformPoint(500.0, 1.0),
                WaveformPoint(1000.0, 0.2),
            ),
            interpolation="cubic",
            update_hz=4,
        ).with_settings(2.0, 500.0)

        restored, locked_times = curve_designer_checkpoint_from_text(
            curve_designer_checkpoint_text(program, (500.0,)),
            update_hz=4,
        )

        self.assertEqual(restored.interpolation, "cubic")
        self.assertEqual(restored.period_ms, 500.0)
        self.assertEqual(restored.max_flow_ml_s, 2.0)
        self.assertEqual(restored.to_json()["control_points"], program.to_json()["control_points"])
        self.assertEqual(locked_times, (250.0,))

    def test_curve_designer_csv_exports_physical_curve_and_closing_point(self):
        program = program_from_control_points(
            (
                WaveformPoint(0.0, 0.0),
                WaveformPoint(500.0, 1.0),
                WaveformPoint(1000.0, 0.0),
            ),
            update_hz=4,
        ).with_settings(2.0, 500.0)

        text = curve_designer_csv_text(program)
        restored = program_from_text(text, update_hz=4)

        self.assertIn("# Columns: time_ms,flow_ml_s", text)
        self.assertEqual(restored.period_ms, 500.0)
        self.assertEqual(restored._commanded_flows(), program._commanded_flows())

    def test_initializes_constant_curve_with_two_control_points(self):
        program = constant_control_point_program(0.4, update_hz=4)
        payload = program.to_json()

        self.assertEqual(program.interpolation, "pchip")
        self.assertEqual(program.samples, (32767, 32767, 32767, 32767))
        self.assertEqual(
            [(point.time_ms, point.flow_ml_s) for point in program.points],
            [(0.0, 0.4), (1000.0, 0.4)],
        )
        self.assertEqual(len(payload["control_points"]), 2)

    def test_fits_requested_control_points_at_largest_curve_errors(self):
        fit = control_point_program_from_text(
            "0 0\n250 0\n500 1\n750 0\n1000 0\n",
            3,
            update_hz=4,
        )

        self.assertEqual(fit.source_point_count, 5)
        self.assertEqual(
            [point.time_ms for point in fit.program.points],
            [0.0, 500.0, 1000.0],
        )
        self.assertAlmostEqual(fit.max_error_ml_s, 0.5)
        self.assertAlmostEqual(fit.rmse_ml_s, (2 * 0.5**2 / 5) ** 0.5)

    def test_control_point_curve_is_shape_preserving(self):
        fit = control_point_program_from_text(
            "0 0\n250 0.2\n500 1\n750 0.2\n1000 0\n",
            5,
            update_hz=16,
        )

        flows = [point["flow_ml_s"] for point in fit.program.to_json()["preview"]]
        self.assertGreaterEqual(min(flows), 0.0)
        self.assertLessEqual(max(flows), 1.0)

    def test_supports_three_control_point_interpolation_strategies(self):
        program = program_from_control_points(
            (
                WaveformPoint(0.0, 0.2),
                WaveformPoint(200.0, 1.0),
                WaveformPoint(600.0, -0.5),
                WaveformPoint(1000.0, 0.9),
            ),
            update_hz=20,
        )

        linear_flows = program.with_interpolation("linear")._resampled_flows()
        pchip_flows = program.with_interpolation("pchip")._resampled_flows()
        cubic_flows = program.with_interpolation("cubic")._resampled_flows()
        self.assertNotEqual(linear_flows, pchip_flows)
        self.assertNotEqual(pchip_flows, cubic_flows)
        self.assertEqual(program.points[-1].flow_ml_s, program.points[0].flow_ml_s)
        for interpolation in ("pchip", "cubic"):
            slopes = _control_point_slopes(program.points, interpolation)
            self.assertAlmostEqual(slopes[0], slopes[-1])
        self.assertEqual(
            len(program.with_interpolation("linear").to_json()["control_points"]),
            4,
        )
        with self.assertRaisesRegex(ValueError, "interpolation must be one of"):
            program.with_interpolation("nearest")

    def test_rejects_invalid_control_point_count(self):
        with self.assertRaisesRegex(ValueError, "between 2 and 20"):
            control_point_program_from_text("0 0\n1000 0\n", 1)
        with self.assertRaisesRegex(ValueError, "between 2 and 20"):
            control_point_program_from_text(
                "\n".join(f"{index * 4} {index}" for index in range(21)),
                21,
            )
        with self.assertRaisesRegex(ValueError, "choose 2 or fewer"):
            control_point_program_from_text("0 0\n1000 0\n", 3)

    def test_accepts_twenty_control_points(self):
        text = "\n".join(f"{index * 4} {index}" for index in range(20))

        fit = control_point_program_from_text(text, 20, update_hz=250)

        self.assertEqual(len(fit.program.points), 20)

    def test_adds_point_on_existing_curve_and_can_remove_it(self):
        program = program_from_control_points(
            (
                WaveformPoint(0.0, 0.0),
                WaveformPoint(500.0, 1.0),
                WaveformPoint(1000.0, 0.0),
            ),
            update_hz=4,
        )

        inserted = insert_control_point(program, 250.0)

        self.assertEqual(len(inserted.points), 4)
        self.assertEqual(inserted.points[1].time_ms, 250.0)
        self.assertAlmostEqual(inserted.points[1].flow_ml_s, 0.5)
        self.assertEqual(remove_control_point(inserted, 1).points, program.points)

    def test_rejects_endpoint_removal_and_more_than_twenty_points(self):
        program = constant_control_point_program(0.4, update_hz=4)
        with self.assertRaisesRegex(ValueError, "define the curve period"):
            remove_control_point(program, 0)

        twenty_points = program_from_control_points(
            tuple(WaveformPoint(index * 100.0, 0.4) for index in range(20)),
            update_hz=10,
        )
        with self.assertRaisesRegex(ValueError, "maximum of 20"):
            insert_control_point(twenty_points, 50.0)

    def test_moves_interior_point_and_keeps_endpoint_times_fixed(self):
        program = program_from_control_points(
            (
                WaveformPoint(0.0, 0.0),
                WaveformPoint(500.0, 1.0),
                WaveformPoint(1000.0, 0.0),
            ),
            interpolation="cubic",
            update_hz=4,
        )

        moved = move_control_point(program, 1, 600.0, 0.7)
        endpoint_moved = move_control_point(moved, 0, 200.0, -0.2)

        self.assertEqual(moved.interpolation, "cubic")
        self.assertEqual(moved.points[1], WaveformPoint(600.0, 0.7))
        self.assertEqual(endpoint_moved.points[0], WaveformPoint(0.0, -0.2))
        self.assertEqual(endpoint_moved.points[-1], WaveformPoint(1000.0, -0.2))
        with self.assertRaisesRegex(ValueError, "cannot cross"):
            move_control_point(program, 1, 1000.0, 0.5)

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

    def test_rounds_trigger_duration_to_nearest_raster(self):
        self.assertEqual(round_to_raster_ms(10.0, 250), 12.0)
        self.assertEqual(round_to_raster_ms(10.0, 500), 10.0)

    def test_negative_flow_clipping_and_total_stroke_volume(self):
        program = program_from_text("0 1\n500 -1\n1000 1\n", update_hz=4)
        clipped = program.with_clip_negative_flow(True)

        self.assertAlmostEqual(program.total_stroke_volume_ml, 0.0)
        self.assertAlmostEqual(program.to_json()["total_stroke_volume_ml"], 0.0)
        self.assertEqual(clipped.samples, (32767, 0, 0, 0))
        self.assertEqual(clipped.negative_clipped_sample_count, 1)
        self.assertAlmostEqual(clipped.total_stroke_volume_ml, 0.25)

    def test_stroke_volume_is_signed_and_scales_with_period(self):
        for flow, expected_volume in ((1.0, 2.0), (-1.0, -2.0), (0.0, 0.0)):
            with self.subTest(flow=flow):
                program = program_from_text(f"0 {flow}\n2000 {flow}\n", update_hz=4)
                self.assertAlmostEqual(program.total_stroke_volume_ml, expected_volume)
        program = program_from_text("0 2\n500 -1\n1000 2\n", update_hz=4)
        # Samples are 2, 0.5, -1, 0.5 mL/s, each held for 0.25 s.
        self.assertAlmostEqual(program.total_stroke_volume_ml, 0.5)
        self.assertAlmostEqual(program.with_settings(2, 2000).total_stroke_volume_ml, 1.0)
        self.assertAlmostEqual(program.to_json(1.0)["total_stroke_volume_ml"], 0.25)

    def test_cubic_preview_export_and_volume_match_transmitted_samples(self):
        for sign in (1, -1):
            program = program_from_control_points(
                tuple(WaveformPoint(t, sign * f) for t, f in (
                    (0, 0), (100, 1), (600, 0.5), (1000, 0),
                )),
                interpolation="cubic",
                update_hz=500,
            )
            for limit in (2.0, 0.75, 0.0):
                with self.subTest(sign=sign, limit=limit):
                    payload = program.to_json(limit)
                    peak = payload["applied_max_flow_ml_s"]
                    encoded = [sample / 32767 * peak
                               for sample in program.samples_with_flow_limit(limit)]
                    for point, flow in zip(payload["preview"], encoded, strict=True):
                        self.assertAlmostEqual(point["flow_ml_s"], flow, delta=peak / 32767)
                    self.assertAlmostEqual(payload["total_stroke_volume_ml"],
                                           sum(encoded) / 500, delta=max(1e-12, peak / 32767))
                    self.assertGreater(payload["clipped_sample_count"], 0)
            payload = program.to_json()
            exported = program_from_text(curve_designer_csv_text(program), update_hz=500)
            self.assertAlmostEqual(exported.total_stroke_volume_ml,
                                   payload["total_stroke_volume_ml"])
            self.assertAlmostEqual(program.total_stroke_volume_ml,
                                   payload["total_stroke_volume_ml"])
            self.assertLessEqual(max(abs(p["flow_ml_s"]) for p in payload["preview"]), 1.0)

    def test_clips_scaled_waveform_to_external_flow_limit(self):
        program = program_from_text("0 0\n500 2\n1000 0\n", update_hz=4)
        payload = program.to_json(max_flow_limit_ml_s=1.0)

        self.assertEqual(program.max_flow_ml_s, 2.0)
        self.assertEqual(payload["max_flow_ml_s"], 2.0)
        self.assertEqual(payload["applied_max_flow_ml_s"], 1.0)
        self.assertEqual(payload["clipped_sample_count"], 1)
        self.assertEqual(max(point["flow_ml_s"] for point in payload["preview"]), 1.0)
        self.assertEqual(
            program.samples_with_flow_limit(1.0),
            (0, 32767, 32767, 32767),
        )

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
