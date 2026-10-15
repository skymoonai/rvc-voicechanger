# Inference-only derivative of Resemble AI / resemble-enhance (MIT).
# Source tree: 8e978149bfe8abab3eb77d965d579a111afdb0ff.
# Training/plotting imports removed; see assets/enhance/THIRD_PARTY_NOTICES.txt.
from .denoiser import Denoiser
from .hparams import HParams

def load_denoiser(run_dir, device):
    # The complete enhancer checkpoint includes these denoiser parameters.
    if run_dir is not None:
        raise ValueError('External training runs are unsupported by the inference-only runtime.')
    return Denoiser(HParams())
