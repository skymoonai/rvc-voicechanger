"""AP-BWE CPU binary pipe worker. Not a microphone capture/playback process."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.rvc_apbwe_cpu import receive_packet, send_packet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--config',required=True)
    args = parser.parse_args()
    import torch
    import numpy as np
    # Confined to this subprocess; never changes RVC's Torch thread settings.
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    from tools.rvc_apbwe_runtime import APBWERuntime
    runtime = APBWERuntime(args.checkpoint,args.config,'cpu')
    send_packet(sys.stdout.buffer,{'ok':True,'ready':True,'frames':0})
    while True:
        try:
            data,raw = receive_packet(sys.stdin.buffer)
        except EOFError:
            return
        try:
            audio = torch.from_numpy(np.frombuffer(raw,dtype='<f4').copy())
            if data.get('reset'):
                runtime.reset()
            out = runtime.process(audio,int(data['rate']),int(data['block_frames']),float(data['strength']))
            send_packet(sys.stdout.buffer,{'ok':True,'frames':len(out)},out.numpy().astype('<f4',copy=False).tobytes())
        except Exception as exc:
            send_packet(sys.stdout.buffer,{'ok':False,'error':str(exc),'frames':0})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        send_packet(sys.stdout.buffer,{'ok':False,'error':str(exc),'frames':0})
