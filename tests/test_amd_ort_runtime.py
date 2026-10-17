"""No GPU required: executable regression checks for ORT DirectML audio engine."""
import sys
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from tools import rvc_ort_worker as worker
REAL_SESSION = worker.session


class FakeInput:
    def __init__(self, name, shape):
        self.name, self.shape = name, shape


class FakeSession:
    def __init__(self, role):
        self.role = role
    def get_inputs(self):
        if self.role == "rvc":
            return [
                FakeInput("feats", [1, "T", 768]),
                FakeInput("p_len", [1]),
                FakeInput("pitch", [1, "T"]),
                FakeInput("pitchf", [1, "T"]),
                FakeInput("sid", [1]),
            ]
        if self.role == "content":
            return [FakeInput("audio", [1, "N"])]
        return [FakeInput("waveform", [1, "N"]),
                FakeInput("threshold", [1])]
    def get_outputs(self):
        if self.role == "content":
            return [FakeInput("last_hidden_state", [1, "T", 768])]
        if self.role == "pitch":
            return [FakeInput("pitchf", [1, "T"])]
        return [FakeInput("audio", [1, "N"])]
    def get_modelmeta(self):
        return types.SimpleNamespace(custom_metadata_map={"samplingRate": "40000"})
    def get_providers(self):
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    def run(self, wanted, inputs):
        if self.role == "content":
            n = inputs["audio"].shape[1] // 320
            return [np.full((1, n, 768), .2, np.float32)]
        if self.role == "pitch":
            n = inputs["waveform"].shape[1] // 160
            return [np.full((1, n), 145, np.float32)]
        assert inputs["feats"].shape[-1] == 768
        assert inputs["pitchf"].shape[1] == inputs["feats"].shape[1]
        assert inputs["pitch"].dtype == np.int64
        return [np.full((1, 1, inputs["feats"].shape[1] * 400), .1, np.float32)]


class OrtdmlTests(unittest.TestCase):
    def setUp(self):
        self.source = patch.object(worker, "session", side_effect=lambda name:
                                   FakeSession({"rvc.onnx": "rvc",
                                                "content.onnx": "content",
                                                "rmvpe.onnx": "pitch"}[name]))
        self.source.start()
        self.addCleanup(self.source.stop)

    def test_pitch_coarse_preserves_unvoiced(self):
        p = worker.pitch_coarse(np.array([0., 100., 440., 1200.]))
        self.assertEqual(p.dtype, np.int64)
        self.assertTrue(np.all((1 <= p) & (p <= 255)))

    def test_pth_to_onnx_metadata_and_frame_geometry(self):
        pipeline = worker.RvcPipeline("rvc.onnx", "content.onnx", "rmvpe.onnx")
        audio = np.zeros(12800, dtype=np.float32)
        output, elapsed = pipeline.convert(audio, 6400)
        self.assertEqual(len(output), 16000)
        self.assertAlmostEqual(float(output.mean()), .1, places=5)
        self.assertGreaterEqual(elapsed, 0)

    def test_live_matches_requested_chunk_length(self):
        pipeline = worker.RvcPipeline("rvc.onnx", "content.onnx", "rmvpe.onnx")
        history = np.random.default_rng(22).normal(size=38400).astype(np.float32) * .03
        result, _elapsed, _rms = worker.convert_chunk(pipeline, history, 400, 400)
        self.assertEqual(len(result), 19200)
        self.assertTrue(np.isfinite(result).all())

    def test_true_passthrough_no_ort_inference(self):
        history = np.ones(19200, np.float32) * .2
        samples, infer, rms = worker.convert_chunk(None, history, 400, 400,
                                                     passthrough=True)
        self.assertEqual(len(samples), 19200)
        self.assertEqual(infer, 0)
        self.assertAlmostEqual(rms, .2, places=5)

    def test_detects_unspecified_onnx_sample_rate(self):
        class MissingMeta(FakeSession):
            def get_modelmeta(self):
                return types.SimpleNamespace(custom_metadata_map={})
        with self.assertRaisesRegex(worker.EngineError, "ornekleme"):
            worker.sample_rate_from_metadata(MissingMeta("rvc"))

    def test_dml_options_are_sequential_and_mem_pattern_disabled(self):
        options = types.SimpleNamespace(
            enable_mem_pattern=True, execution_mode=None, graph_optimization_level=None)
        observed = []
        class ORT:
            class ExecutionMode:
                ORT_SEQUENTIAL = "seq"
            class GraphOptimizationLevel:
                ORT_ENABLE_ALL = "all"
            @staticmethod
            def SessionOptions():
                return options
            @staticmethod
            def InferenceSession(path, sess_options, providers):
                observed.append((path, providers))
                return FakeSession("rvc")
        with patch.dict(sys.modules, {"onnxruntime": ORT}):
            REAL_SESSION("test.onnx")
        self.assertFalse(options.enable_mem_pattern)
        self.assertEqual(options.execution_mode, "seq")
        self.assertEqual(observed[0][1][0][0], "DmlExecutionProvider")

    def test_check_wrong_provider_rejects_cpu_only(self):
        class CPU(FakeSession):
            def get_providers(self):
                return ["CPUExecutionProvider"]
        class ORT:
            class ExecutionMode:
                ORT_SEQUENTIAL = "seq"
            class GraphOptimizationLevel:
                ORT_ENABLE_ALL = "all"
            @staticmethod
            def SessionOptions():
                return types.SimpleNamespace()
            @staticmethod
            def InferenceSession(*a, **kw):
                return CPU("rvc")
        with patch.dict(sys.modules, {"onnxruntime": ORT}):
            with self.assertRaisesRegex(worker.EngineError, "DmlExecutionProvider"):
                REAL_SESSION("test.onnx")

    def test_wav_conversion_streams_and_preserves_sample_count(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, target = folder / "in.wav", folder / "out.wav"
            sf.write(source, np.zeros(48000, np.float32), 48000)
            args = types.SimpleNamespace(
                model="rvc.onnx", embedder="content.onnx", f0_model="rmvpe.onnx",
                pitch_shift=0, index="", index_rate=0., protect=.5,
                chunk_ms=400, extra_convert_ms=400, rms_mix_rate=1.,
                silence_threshold=0., input=str(source), output=str(target)
            )
            worker.offline(args)
            data, rate = sf.read(target)
            self.assertEqual(rate, 48000)
            self.assertEqual(len(data), 48000)
            self.assertAlmostEqual(float(np.mean(data)), .1, places=3)

    def test_index_is_independent_from_model_engine(self):
        vectors = np.ones((50,768), np.float32)*.5
        class FAISS:
            d, ntotal = 768, 50
            def reconstruct_n(self, start, count):
                return vectors
            def search(self, source, k):
                n = len(source)
                return np.full((n,k), .1, np.float32), np.zeros((n,k), np.int64)
        class FakeFaiss:
            @staticmethod
            def read_index(path):
                return FAISS()
        with patch.dict(sys.modules, {"faiss": FakeFaiss}):
            pipeline = worker.RvcPipeline("rvc.onnx", "content.onnx", "rmvpe.onnx",
                                           index="voice.index", index_rate=.75, protect=.2)
            mix = pipeline._index_mix(np.ones((1,10,768),np.float32)*.2,
                                      np.zeros(20,np.float32))
            self.assertEqual(mix.shape, (1,20,768))
            self.assertGreater(float(mix.mean()), .2)
            self.assertLess(float(mix.mean()), .5)


if __name__ == "__main__":
    unittest.main()
