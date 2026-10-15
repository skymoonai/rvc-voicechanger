# Inference-only derivative of Resemble AI / resemble-enhance (MIT).
# Source tree: 8e978149bfe8abab3eb77d965d579a111afdb0ff.
# Training/plotting imports removed; see assets/enhance/THIRD_PARTY_NOTICES.txt.

from dataclasses import dataclass
from ..hparams import HParams as HParamsBase

@dataclass(frozen=True)
class HParams(HParamsBase):
    batch_size_per_gpu: int = 128
    distort_prob: float = 0.5
