# Inference-only derivative of Resemble AI / resemble-enhance (MIT).
# Source tree: 8e978149bfe8abab3eb77d965d579a111afdb0ff.
# Training/plotting imports removed; see assets/enhance/THIRD_PARTY_NOTICES.txt.

from dataclasses import dataclass
from pathlib import Path
from ..hparams import HParams as HParamsBase

@dataclass(frozen=True)
class HParams(HParamsBase):
    cfm_solver_method: str = 'midpoint'
    cfm_solver_nfe: int = 64
    cfm_time_mapping_divisor: int = 4
    univnet_nc: int = 96
    lcfm_latent_dim: int = 64
    lcfm_training_mode: str = 'ae'
    lcfm_z_scale: float = 5
    vocoder_extra_dim: int = 32
    gan_training_start_step: int | None = 5000
    enhancer_stage1_run_dir: Path | None = None
    denoiser_run_dir: Path | None = None
    force_gaussian_prior: bool = False
