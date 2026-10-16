"""Regress lisp/blur diagnosis: quality defaults, threshold and audio routing."""
import unittest
from types import SimpleNamespace

from tools.rvc_amd_quality import (
    DEFAULT_PRESET, PRESETS, quality_settings, detection_threshold,
    check_audio_feedback, likely_quality_problems,
)


class VoiceQualityRegressionTests(unittest.TestCase):
    def test_new_default_does_not_blend_almost_half_a_chunk(self):
        self.assertEqual(DEFAULT_PRESET, "netlik")
        props = quality_settings(DEFAULT_PRESET)
        self.assertEqual(props["chunk_ms"], 400)
        self.assertEqual(props["crossfade_ms"], 40)
        self.assertEqual(props["extra_convert_ms"], 400)
        self.assertLessEqual(props["crossfade_ms"] / props["chunk_ms"], .2)

    def test_low_latency_fade_is_short_and_context_is_explicit(self):
        props = quality_settings("dusuk_gecikme", 200)
        self.assertEqual(props, {
            "chunk_ms": 200, "crossfade_ms": 30, "extra_convert_ms": 150
        })

    def test_consonant_sensitive_minimum_disables_gate(self):
        self.assertEqual(detection_threshold(-60), 0.)
        self.assertAlmostEqual(detection_threshold(-40), .01)
        with self.assertRaises(ValueError):
            detection_threshold(10)

    def test_same_vac_default_is_blocked(self):
        vac = "Line 1 (Virtual Audio Cable)"
        with self.assertRaisesRegex(ValueError, "aynı cihaz"):
            check_audio_feedback("", "", vac, vac)
        with self.assertRaises(ValueError):
            check_audio_feedback(vac, vac, "", "")
        check_audio_feedback(vac, "App Sandbox Virtual Audio", vac, vac)

    def test_dropouts_and_rnnoise_are_explained(self):
        m = {
            "processing_ms": 380, "input_overruns": 2,
            "output_underruns": 1, "output_dropped_samples": 200
        }
        notes = likely_quality_problems(m, 400, "rnnoise", -25)
        self.assertGreaterEqual(len(notes), 5)
        self.assertTrue(any("chunk" in note.lower() for note in notes))
        self.assertFalse(likely_quality_problems({}, 400, "off", -60))


if __name__ == "__main__":
    unittest.main()
