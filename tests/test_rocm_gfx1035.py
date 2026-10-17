"""CPU-only tests for experimental Radeon 680M ROCm backend and setup guards."""
import ast
import contextlib
import logging
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Device:
    def __init__(self, spec):
        self.type = spec.split(":")[0]
        self.index = 0 if ":" in spec else None
    def __str__(self):
        return self.type + (":0" if self.index is not None else "")


class Scalar:
    shape = (1, 1, 128)
    def __getitem__(self, item):
        return self
    def cpu(self):
        return self
    def __float__(self):
        return 3.0


class MockTorch:
    float32 = object()
    def __init__(self, fail=False):
        self.fail = fail
        self.cuda = self
        self.nn = type("NN", (), {"functional": self})()
    def device(self, name):
        return Device(name)
    def is_available(self):
        return True
    def device_count(self):
        return 1
    def get_device_capability(self, _):
        return (0, 0)
    def get_device_name(self, _):
        return "AMD Radeon 680M"
    def get_device_properties(self, _):
        # Important: UMA dedicated aperture can be far below 4 GiB.
        return type("Properties", (), {"total_memory": 512*1024*1024})()
    def inference_mode(self):
        return contextlib.nullcontext()
    def ones(self, *args, **kwargs):
        return Scalar()
    def conv1d(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("No HIP queues: GPU-PV only")
        return Scalar()
    def synchronize(self, device):
        if self.fail:
            raise RuntimeError("synchronize failed")


def load_device_selector(torch):
    tree = ast.parse((ROOT / "configs" / "config.py").read_text(encoding="utf-8"))
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == "get_device_dtype_sm")
    block = ast.Module(body=[fn], type_ignores=[])
    namespace = {"torch": torch, "ROCM_BUILD": True, "logger": logging.getLogger("test"),
                 "re": re}
    exec(compile(block, "<config.selector>", "exec"), namespace)
    return namespace["get_device_dtype_sm"]


class Rocm680MPreviewTests(unittest.TestCase):
    def test_uma_512mb_not_rejected_by_nvidia_four_gib_cutoff(self):
        torch = MockTorch()
        device, dtype, _, memory = load_device_selector(torch)(0)
        self.assertEqual(device.type, "cuda")
        self.assertIs(dtype, torch.float32)
        self.assertLess(memory, 4)

    def test_fake_gpu_without_hip_queues_is_rejected(self):
        torch = MockTorch(fail=True)
        device, dtype, _, _ = load_device_selector(torch)(0)
        self.assertEqual(device.type, "cpu")
        self.assertIs(dtype, torch.float32)

    def test_launcher_refuses_cpu_fallback(self):
        source = (ROOT / "scripts/run_rocm_680m_preview.ps1").read_text(encoding="utf-8")
        self.assertIn("scripts/check_rocm.py", source)
        self.assertIn("RVC_ROCM_REQUIRE_GFX", source)
        self.assertIn("throw 'Real Radeon 680M HIP GPU test failed", source)
        self.assertIn("modern_launcher.py", source)

    def test_setup_isolated_and_requires_explicit_nightly_approval(self):
        source = (ROOT / "scripts/setup_rocm_680m_preview.ps1").read_text(encoding="utf-8")
        for required in ("-AcceptNightlyRisk", "device-gfx1035",
                         ".venv-rocm-gfx1035", "-AllowVirtualizedGpu"):
            self.assertIn(required, source)
        for risky in ("HSA_OVERRIDE_GFX_VERSION", "Disable-WindowsOptionalFeature",
                      "Remove-Item C:\\RVCStudio", "Set-MpPreference"):
            self.assertNotIn(risky, source)

    def test_hip_arch_preflight_required(self):
        source = (ROOT / "scripts/check_rocm.py").read_text(encoding="utf-8")
        self.assertIn("RVC_ROCM_REQUIRE_GFX", source)
        self.assertIn("torch.cuda.synchronize", source)
        self.assertIn("return 6", source)


if __name__ == "__main__":
    unittest.main()
