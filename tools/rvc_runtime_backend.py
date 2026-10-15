"""RVC Studio real-time inference backend selection (CPU/CUDA/DirectML).

The selection changes the active RVC inference engine, not merely a UI label.
It does not install GPU drivers or PyTorch backends.
"""

MODES = ('Otomatik', 'CPU', 'Ekran Kartı (GPU)')


def choose_backend(mode):
    """Resolve a user choice without modifying the current engine.

    Raises when GPU was requested but not actually available in this installation.
    """
    from configs import config as cfg
    import torch

    if mode not in MODES:
        mode = MODES[0]
    if mode == 'CPU':
        return torch.device('cpu'), torch.float32, 'CPU'

    if mode == 'Otomatik':
        device, dtype = cfg.infer_device, cfg.infer_dtype
        kind = getattr(device, 'type', str(device).split(':')[0])
        label = 'NVIDIA CUDA' if kind == 'cuda' else ('AMD / Intel DirectML' if kind == 'privateuseone' else 'CPU')
        return device, dtype, label

    profiles = [profile for profile in cfg.GPU_PROFILES if profile[0].type == 'cuda']
    if profiles:
        device, dtype, _, _ = max(profiles, key=lambda p: (p[2], p[3]))
        return device, dtype, 'NVIDIA CUDA'
    if cfg.DML_AVAILABLE and cfg.DML_DEVICE is not None:
        return cfg.DML_DEVICE, torch.float32, 'AMD / Intel DirectML'
    raise RuntimeError(
        'Bu kurulumda kullanılabilir GPU hızlandırması bulunamadı. '
        'NVIDIA için CUDA, AMD/Intel için DirectML profiliyle kurulum yapın '
        've ekran kartı sürücünüzü kontrol edin. CPU seçeneğini kullanabilirsiniz.'
    )


def apply_backend(config, mode):
    """Set the shared config BEFORE model/stream creation; return device key and display.

    Existing model caches must be disposed of by the caller when the key changes.
    """
    from configs import config as cfg
    import torch

    device, dtype, label = choose_backend(mode)
    kind = getattr(device, 'type', str(device).split(':')[0])
    key = (kind, str(device), str(dtype))
    config.device = device
    config.dtype = dtype
    config.is_half = dtype == torch.float16
    config.dml = kind == 'privateuseone'
    config.cuda_graph = bool(kind == 'cuda' and cfg.CUDA_GRAPH_AVAILABLE)
    config.instead = 'cpu' if kind == 'cpu' else ''
    config.gpu_name = None
    config.gpu_mem = None
    config.preprocess_per = 3.0
    if kind == 'cuda':
        index = device.index if device.index is not None else 0
        for cand_device, _, _, mem in cfg.GPU_PROFILES:
            if cand_device.type == 'cuda' and cand_device.index == index:
                config.gpu_mem = int(mem)
                break
        config.gpu_name = torch.cuda.get_device_name(index)
    print('[Hesaplama aygıtı] Tercih: %s; etkin: %s (%s); fp16: %s' %
          (mode, label, device, config.is_half), flush=True)
    return key, '%s (%s)' % (label, device)
