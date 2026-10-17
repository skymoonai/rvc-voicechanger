"""No GPU required: strict ROCm must prove HIP, not manufacture it."""
from __future__ import annotations

import contextlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.rvc_rocm_strict import (
    FORCE_MODE, RocmUnavailable, verify_gpu, wants_force,
)
from tools import rvc_runtime_backend

ROOT = Path(__file__).resolve().parents[1]


class ToyTensor:
    def __init__(self, shape):
        self.shape = shape

    def __truediv__(self, scalar):
        return ToyTensor(self.shape)

    def reshape(self, *shape):
        return ToyTensor((1, 1, 4096))

    def all(self):
        return self

    def item(self):
        return True


class ToyTorch:
    float32 = "float32"

    def __init__(self, hip="7.2.1", arch="gfx1035:sramecc-:xnack-",
                 gpu=True, kernel_error=False):
        self.version = types.SimpleNamespace(hip=hip)
        self.cuda = self
        self.nn = types.SimpleNamespace(functional=self)
        self.arch, self.gpu, self.kernel_error = arch, gpu, kernel_error

    def device(self, value):
        return types.SimpleNamespace(type="cuda", index=0, name=value)

    def is_available(self):
        return self.gpu

    def device_count(self):
        return 1 if self.gpu else 0

    def get_device_properties(self, _device):
        return types.SimpleNamespace(gcnArchName=self.arch)

    def get_device_name(self, _index):
        return "AMD Radeon 680M"

    def inference_mode(self):
        return contextlib.nullcontext()

    def linspace(self, *_args, **_kwargs):
        return ToyTensor((4096,))

    def ones(self, *_args, **_kwargs):
        return ToyTensor((1, 1, 9))

    def conv1d(self, *_args, **_kwargs):
        if self.kernel_error:
            raise RuntimeError("HIP driver missing")
        return ToyTensor((1, 1, 4096))

    def stft(self, *_args, **_kwargs):
        return ToyTensor((257, 33))

    def synchronize(self, _device):
        if self.kernel_error:
            raise RuntimeError("HIP queue failed")

    def isfinite(self, _tensor):
        return ToyTensor((4096,))


class StrictRocmTests(unittest.TestCase):
    def test_hip_gpu_and_arch_accepted(self):
        device, name, arch = verify_gpu(ToyTorch(), required_gfx="gfx1035")
        self.assertEqual(device.type, "cuda")
        self.assertIn("680M", name)
        self.assertTrue(arch.startswith("gfx1035"))

    def test_cpu_cuda_build_cannot_be_relabelled_rocm(self):
        with self.assertRaisesRegex(RocmUnavailable, "ROCm/HIP"):
            verify_gpu(ToyTorch(hip=None), required_gfx="gfx1035")

    def test_virtual_gpu_label_without_hip_queues_refused(self):
        with self.assertRaisesRegex(RocmUnavailable, "HIP GPU"):
            verify_gpu(ToyTorch(gpu=False), required_gfx="gfx1035")
        with self.assertRaisesRegex(RocmUnavailable, "çalıştıramadı"):
            verify_gpu(ToyTorch(kernel_error=True), required_gfx="gfx1035")

    def test_wrong_gpu_architecture_does_not_use_override(self):
        with self.assertRaisesRegex(RocmUnavailable, "mimarisi uyuşmuyor"):
            verify_gpu(ToyTorch(arch="gfx1030"), required_gfx="gfx1035")

    def test_explicit_mode_or_flag_requests_strict(self):
        with patch.dict(os.environ, {"RVC_ROCM_FORCE": "0"}):
            self.assertTrue(wants_force(FORCE_MODE))
            self.assertFalse(wants_force("CPU"))
        with patch.dict(os.environ, {"RVC_ROCM_FORCE": "1"}):
            self.assertTrue(wants_force("CPU"))
            self.assertTrue(wants_force("Otomatik"))

    def test_choosing_cpu_cannot_fallback_under_forced_environment(self):
        fake = ToyTorch()
        config = types.ModuleType("configs.config")
        config.ROCM_BUILD = True
        pkg = types.ModuleType("configs")
        pkg.__path__ = []
        pkg.config = config
        with patch.dict(sys.modules,
                        {"torch": fake, "configs": pkg, "configs.config": config}):
            with patch.dict(os.environ, {"RVC_ROCM_FORCE": "1",
                                         "RVC_ROCM_REQUIRE_GFX": "gfx1035"}):
                device, dtype, label = rvc_runtime_backend.choose_backend("CPU")
                self.assertEqual(device.type, "cuda")
                self.assertEqual(dtype, fake.float32)
                self.assertIn("ROCm", label)

    def test_existing_unforced_cpu_mode_still_available(self):
        fake = ToyTorch()
        fake.device = lambda value: types.SimpleNamespace(type=value, index=None)
        config = types.ModuleType("configs.config")
        config.ROCM_BUILD = True
        pkg = types.ModuleType("configs")
        pkg.__path__ = []
        pkg.config = config
        with patch.dict(sys.modules,
                        {"torch": fake, "configs": pkg, "configs.config": config}):
            with patch.dict(os.environ, {"RVC_ROCM_FORCE": "0"}):
                device, _, _ = rvc_runtime_backend.choose_backend("CPU")
                self.assertEqual(device.type, "cpu")

    def test_gui_and_launcher_offer_real_opt_in(self):
        ui = (ROOT / "tools/rvc_modern_qt.py").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts/run_rocm_680m_preview.ps1").read_text(
            encoding="utf-8")
        self.assertIn(FORCE_MODE, ui)
        self.assertIn("[switch]$ForceROCm", launcher)
        self.assertIn("RVC_ROCM_FORCE", launcher)
        self.assertNotIn("HSA_OVERRIDE_GFX_VERSION", launcher)


if __name__ == "__main__":
    unittest.main()
