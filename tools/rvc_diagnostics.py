"""Bounded UI log and targeted FAISS retrieval diagnostics for the modern launcher."""
from __future__ import annotations

import queue
import threading
import time

LOG_MESSAGES = queue.Queue(maxsize=1024)


def publish(text):
    if not text:
        return
    # Keep the newest messages when the UI is busy loading a model.
    for offset in range(0, len(text), 8192):
        chunk = text[offset:offset + 8192]
        try:
            LOG_MESSAGES.put_nowait(chunk)
        except queue.Full:
            try:
                LOG_MESSAGES.get_nowait()
            except queue.Empty:
                pass
            try:
                LOG_MESSAGES.put_nowait(chunk)
            except queue.Full:
                pass


def drain(limit=100):
    chunks = []
    for _ in range(limit):
        try:
            chunks.append(LOG_MESSAGES.get_nowait())
        except queue.Empty:
            break
    return ''.join(chunks)


class IndexSearchGuard:
    """Retry only incomplete IVF queries; preserve successful retrieval results."""
    def __init__(self, index, path, faiss):
        self.index, self.path = index, str(path)
        self.ivf = None
        self.last_warning = -float('inf')
        self.recovery_reported = False
        self.problem = ''
        try:
            self.ivf = faiss.extract_index_ivf(index)
        except Exception:
            pass
        print(f'[İndeks] Dosya: {self.path} | vektör: {index.ntotal} | boyut: {index.d} | eğitilmiş: {index.is_trained}', flush=True)

    def __getattr__(self, name):
        return getattr(self.index, name)

    def search(self, queries, k):
        import numpy as np
        if queries.shape[1] != self.index.d:
            raise ValueError(f'İndeks/model boyutu uyuşmuyor: model={queries.shape[1]}, indeks={self.index.d}. Dosya: {self.path}')
        # A populated but tiny added index cannot supply eight distinct neighbors.
        count = min(k, int(self.index.ntotal)) if self.index.ntotal else k
        score, ids = self.index.search(queries, count)
        missing = np.any(ids < 0, axis=1)
        if missing.any() and self.ivf is not None and self.index.ntotal:
            before = int(self.ivf.nprobe)
            cap = min(int(self.ivf.nlist), max(before, 16))
            try:
                for probe in (min(cap, max(1, before) * 2), min(cap, max(1, before) * 4)):
                    if probe <= int(self.ivf.nprobe):
                        continue
                    self.ivf.nprobe = probe
                    retry_score, retry_ids = self.index.search(queries[missing], count)
                    score[missing], ids[missing] = retry_score, retry_ids
                    missing = np.any(ids < 0, axis=1)
                    if not missing.any():
                        if not self.recovery_reported:
                            print(f'[İndeks] Eksik eşleşmeler daha geniş aramayla tamamlandı (nprobe {before} → {probe}). Dosya: {self.path}', flush=True)
                            self.recovery_reported = True
                        break
            finally:
                self.ivf.nprobe = before
        if missing.any():
            reason = 'İndeks boş; içinde ses özellikleri yok.' if not self.index.ntotal else f'{int(missing.sum())}/{len(ids)} sorguda {count} geçerli eşleşme bulunamadı.'
            self.problem = f'[İndeks uyarısı] {reason} Dosya: {self.path} | toplam vektör: {self.index.ntotal}. Dosya adı tek başına geçerlilik göstergesi değildir; bu blokta indeks katkısı uygulanmadı.'
        return score, ids

    def warn(self):
        now = time.monotonic()
        if now - self.last_warning >= 10:
            print(self.problem or f'[İndeks uyarısı] Arama eksik sonuç döndürdü. Dosya: {self.path}', flush=True)
            self.last_warning = now


def install_index_hooks(module):
    cls = module.RVC
    if getattr(cls, '_studio_index_hooks', False):
        return
    cls._studio_index_hooks = True
    context = threading.local()
    original_init, original_infer = cls.__init__, cls.infer
    original_change, original_print = cls.change_index_rate, module.printt
    misleading = module.i18n('索引无效：必须使用added_xxxx.index，不能使用trained_xxxx.index')

    def wrap(owner, path):
        index = getattr(owner, 'index', None)
        if index is not None and not isinstance(index, IndexSearchGuard):
            owner.index = IndexSearchGuard(index, path, module.faiss)

    def init(owner, *args, **kwargs):
        original_init(owner, *args, **kwargs)
        path = kwargs.get('index_path', args[3] if len(args) > 3 else getattr(owner, 'index_path', ''))
        wrap(owner, path)

    def change(owner, *args, **kwargs):
        original_change(owner, *args, **kwargs)
        wrap(owner, getattr(owner, 'index_path', ''))

    def infer(owner, *args, **kwargs):
        previous = getattr(context, 'index', None)
        context.index = getattr(owner, 'index', None)
        try:
            return original_infer(owner, *args, **kwargs)
        finally:
            context.index = previous

    def printt(message, *args):
        index = getattr(context, 'index', None)
        if message == misleading and isinstance(index, IndexSearchGuard):
            index.warn()
        else:
            original_print(message, *args)

    cls.__init__, cls.change_index_rate, cls.infer = init, change, infer
    module.printt = printt