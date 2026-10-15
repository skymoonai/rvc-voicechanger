# Inference-only derivative of Resemble AI / resemble-enhance (MIT).
# Source tree: 8e978149bfe8abab3eb77d965d579a111afdb0ff.
# Training/plotting imports removed; see assets/enhance/THIRD_PARTY_NOTICES.txt.
from dataclasses import dataclass
from pathlib import Path

def _make_stft_cfg(hop_length, win_length=None):
    if win_length is None:
        win_length = 4 * hop_length
    n_fft = 2 ** (win_length - 1).bit_length()
    return dict(n_fft=n_fft, hop_length=hop_length, win_length=win_length)

@dataclass(frozen=True)
class HParams:
    fg_dir: Path = Path('data/fg')
    bg_dir: Path = Path('data/bg')
    rir_dir: Path = Path('data/rir')
    load_fg_only: bool = False
    praat_augment_prob: float = 0
    wav_rate: int = 44100
    n_fft: int = 2048
    win_size: int = 2048
    hop_size: int = 420
    num_mels: int = 128
    stft_magnitude_min: float = 0.0001
    preemphasis: float = 0.97
    mix_alpha_range: tuple[float, float] = (0.2, 0.8)
    nj: int = 64
    training_seconds: float = 1.0
    batch_size_per_gpu: int = 16
    min_lr: float = 1e-05
    max_lr: float = 0.0001
    warmup_steps: int = 1000
    max_steps: int = 1000000
    gradient_clipping: float = 1.0

    @property
    def stft_cfgs(self):
        assert self.wav_rate == 44100, f'wav_rate must be 44_100, got {self.wav_rate}'
        return [_make_stft_cfg(h) for h in (100, 256, 512)]
