"""CPU-only tests for ROCm backend labeling (no AMD GPU required)."""
import sys
import types
import unittest
from unittest.mock import patch

import torch
from tools import rvc_runtime_backend


class BackendSelectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = types.ModuleType("configs.config")
        self.cfg.ROCM_BUILD = True
        self.cfg.GPU_PROFILES = [(torch.device("cuda:0"), torch.float32, 0.0, 8.0)]
        self.cfg.infer_device = torch.device("cuda:0")
        self.cfg.infer_dtype = torch.float32
        self.cfg.DML_AVAILABLE = False
        self.cfg.DML_DEVICE = None
        self.cfg.CUDA_GRAPH_AVAILABLE = True
        self.pkg = types.ModuleType("configs")
        self.pkg.config = self.cfg
        self.patch_modules = patch.dict(
            sys.modules, {"configs": self.pkg, "configs.config": self.cfg}
        )
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)

    def test_automatic_rocm_is_not_labeled_nvidia(self):
        device, dtype, label = rvc_runtime_backend.choose_backend("Otomatik")
        self.assertEqual(device.type, "cuda")
        self.assertEqual(dtype, torch.float32)
        self.assertEqual(label, "AMD ROCm")

    def test_gpu_rocm_is_not_labeled_nvidia(self):
        _device, _dtype, label = rvc_runtime_backend.choose_backend("Ekran Kartı (GPU)")
        self.assertEqual(label, "AMD ROCm")

    def test_nvidia_build_retains_cuda_label(self):
        self.cfg.ROCM_BUILD = False
        _device, _dtype, label = rvc_runtime_backend.choose_backend("Ekran Kartı (GPU)")
        self.assertEqual(label, "NVIDIA CUDA")

    def test_rocm_disables_cuda_graph_in_app_config(self):
        runtime = types.SimpleNamespace()
        with patch("torch.cuda.get_device_name", return_value="AMD Radeon RX 9070 XT"):
            key, label = rvc_runtime_backend.apply_backend(
                runtime, "Ekran Kartı (GPU)"
            )
        self.assertFalse(runtime.cuda_graph)
        self.assertFalse(runtime.is_half)
        self.assertFalse(runtime.dml)
        self.assertEqual(key[0], "cuda")
        self.assertIn("ROCm", label)

    def test_cpu_fallback(self):
        device, dtype, label = rvc_runtime_backend.choose_backend("CPU")
        self.assertEqual(device.type, "cpu")
        self.assertEqual(dtype, torch.float32)
        self.assertEqual(label, "CPU")

    def test_no_gpu_explains_missing_backend(self):
        self.cfg.GPU_PROFILES = []
        with self.assertRaisesRegex(RuntimeError, "GPU"):
            rvc_runtime_backend.choose_backend("Ekran Kartı (GPU)")


if __name__ == "__main__":
    unittest.main()
