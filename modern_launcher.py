"""Modern Qt adapter and optional output monitoring for RVC 2.3 realtime GUI."""
from __future__ import annotations

import ast
import faulthandler
import os
import re
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'realtime_gui.py'


def preload_compatibility_imports():
    # RVC_OPTIONAL_STARTUP_PRELOAD_V1: warm optional six/dateutil before Qt.
    # The realtime application does not require pandas for this workaround.
    from importlib import import_module
    for name in ('six.moves', 'dateutil.tz'):
        try:
            import_module(name)
        except ModuleNotFoundError as error:
            if error.name not in (name, name.split('.')[0]):
                raise


def prepare_source(original: str) -> str:
    if '# RVC_PROFILES_ADDON_V1' not in original or 'class GUI(ProfileSupport)' not in original:
        raise RuntimeError('Model kütüphanesi yaması bulunamadı. Önce çalışan model kütüphanesini kur.')
    before = '    import FreeSimpleGUI as sg\n'
    if original.count(before) != 1:
        raise RuntimeError('RVC FreeSimpleGUI import satırı beklenen yapıda değil.')
    original = original.replace(before, '    from tools.rvc_modern_qt import sg\n', 1)
    original = original.replace('i18n = I18nAuto()', "from tools.rvc_i18n import LANGUAGE as studio_language\n    i18n = I18nAuto('tr_TR' if studio_language == 'tr' else 'en_US')", 1)
    required_index = '''            if len(values["index_path"].strip()) == 0:
                sg.popup(i18n("请选择index文件"))
                return False
'''
    if original.count(required_index) != 1:
        raise RuntimeError('RVC indeks kontrolü beklenen yapıda değil. Kaynak değiştirilmedi.')
    original = original.replace(required_index, '''            if len(values["index_path"].strip()) == 0:
                values = dict(values, index_rate=0.0)
                self.window["index_rate"].update(0.0)
''', 1)
    pattern = r'(?ms)^        def launcher\(self\):\n.*?(?=^        def event_handler\(self\):)'
    before = '            # SOLA algorithm from https://github.com/yxlllc/DDSP-SVC\n'
    if original.count(before) != 1:
        raise RuntimeError('Canlı AP-BWE bağlantısı için SOLA noktası bulunamadı.')
    original = original.replace(before, '''            # RVC_APBWE_REALTIME_V1: reuse existing RVC context, before SOLA.
            if (self.function == "vc"
                    and getattr(self, "apbwe", None) is not None
                    and getattr(self, "studio_tools", None) is not None
                    and getattr(self, "file_converter", None) is not None
                    and not self.studio_tools.muted
                    and not self.studio_tools.busy
                    and not self.file_converter.busy):
                infer_wav = self.apbwe.process(
                    infer_wav, self.gui_config.samplerate, self.block_frame,
                    deadline=start_time + self.block_frame / self.gui_config.samplerate * 0.95,
                )
''' + before, 1)
    if len(re.findall(pattern, original)) != 1:
        raise RuntimeError('RVC launcher metodu beklenen yapıda değil. Kaynak değiştirilmedi.')
    new_launcher = '''        def launcher(self):
            data = self.load()
            data = self.profile_start_data(data)
            from tools.rvc_modern_qt import create_window
            self.window = create_window(self, data)
            self.event_handler()

'''
    original = re.sub(pattern, lambda _m: new_launcher, original, count=1)
    # RVC_AUDIO_OPTIMIZATIONS_V1: keep upstream source compatibility while
    # avoiding host/device round-trips and per-block disk logging.
    before = '    import librosa\n'
    original = original.replace(before, before + '    from tools.rvc_audio_quality import tensor_rms\n', 1)
    original = original.replace('os.environ["OMP_NUM_THREADS"] = "4"',
                                'os.environ.setdefault("OMP_NUM_THREADS", str(min(4, os.cpu_count() or 1)))')
    original = original.replace('            indata = librosa.to_mono(indata.T)',
                                '            indata = indata[:, 0] if indata.shape[1] == 1 else np.mean(indata, axis=1, dtype=np.float32)')
    for target, argument in (('rms1', 'input_wav[: infer_wav.shape[0]]'), ('rms2', 'infer_wav[:]')):
        expression = (rf'{target} = librosa\.feature\.rms\(\s*'
                      rf'y={re.escape(argument)}\.cpu\(\)\.numpy\(\),\s*'
                      rf'frame_length=4 \* self\.zc,\s*hop_length=self\.zc,\s*\)\s*'
                      rf'{target} = torch\.from_numpy\({target}\)\.to\(self\.config\.device\)')
        original, count = re.subn(expression, f'{target} = tensor_rms({argument}, 4 * self.zc, self.zc)', original)
        if count != 1:
            raise RuntimeError('Ses seviyesi optimizasyon noktası bulunamadı: ' + target)
    original = original.replace('            printt(i18n("SOLA偏移：%d"), int(sola_offset))\n', '')
    output_expression = (r'outdata\[:\] = \(\s*infer_wav\[: self\.block_frame\]\s*'
                         r'\.repeat\(self\.gui_config\.channels, 1\)\s*\.t\(\)\s*'
                         r'\.cpu\(\)\s*\.numpy\(\)\s*\)')
    original, count = re.subn(output_expression, 'outdata[:] = infer_wav[:self.block_frame].detach().cpu().numpy()[:, None]', original)
    if count != 1:
        raise RuntimeError('Ses çıkışı optimizasyon noktası bulunamadı.')
    original = original.replace('                self.window["infer_time"].update(int(total_time * 1000))',
                                '                self._studio_infer_ms = int(total_time * 1000)')
    original = original.replace('            printt(i18n("推理耗时：%.2f秒"), total_time)', '''            if start_time - getattr(self, '_studio_last_dsp_log', 0) >= 5:
                self._studio_last_dsp_log = start_time
                printt(i18n("推理耗时：%.2f秒"), total_time)''')
    before = '    gui = GUI()'
    if original.count(before) != 1:
        raise RuntimeError('RVC GUI başlatma satırı beklenen yapıda değil.')
    original = original.replace(before, '''    from tools.rvc_diagnostics import install_index_hooks
    install_index_hooks(rvc_for_realtime)
    from tools.rvc_monitor import install_engine_hooks
    install_engine_hooks(GUI)
    from tools.rvc_errors import install_engine_guards
    install_engine_guards(GUI)
    from tools.rvc_modern_qt import startup_progress
    startup_progress('Ses cihazları hazırlanıyor...')
    gui = GUI()''', 1)
    for before, label in (
        ('    import librosa\n', 'Ses kütüphaneleri yükleniyor...'),
        ('    import torch\n', 'Ses hesaplama motoru yükleniyor...'),
        ('    from configs.config import Config\n', 'RVC yapılandırması yükleniyor...'),
        ('    from infer import rtrvc as rvc_for_realtime\n', 'RVC dönüşüm motoru yükleniyor...'),
    ):
        if original.count(before) == 1:
            original = original.replace(before, f'    from tools.rvc_modern_qt import startup_progress\n    startup_progress({label!r})\n' + before, 1)
    ast.parse(original, filename=str(SOURCE))
    return original


class Tee:
    def __init__(self, console, logfile):
        self.console, self.logfile = console, logfile
        self.lock = _LOG_LOCK
        self.encoding = 'utf-8'
        self.errors = 'replace'

    def write(self, text):
        from tools.rvc_diagnostics import publish
        with self.lock:
            if self.console:
                self.console.write(text)
            self.logfile.write(text)
            self.logfile.flush()
            publish(text)
        return len(text)

    def flush(self):
        with self.lock:
            if self.console:
                self.console.flush()
            self.logfile.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.console if self.console is not None else self.logfile, name)


_LOG_LOCK = threading.RLock()


def main():
    from tools.rvc_user_state import data_root, consent_valid, begin_session, end_session
    from tools.rvc_errors import CENTER
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    logs=data_root()/'logs'
    logs.mkdir(parents=True,exist_ok=True)
    session=None
    clean=False
    scope=None
    with (logs/'modern_startup.log').open('w',encoding='utf-8',buffering=1) as log:
        stdout,stderr=sys.stdout,sys.stderr
        sys.stdout,sys.stderr=Tee(stdout,log),Tee(stderr,log)
        faulthandler.enable(file=log)
        faulthandler.dump_traceback_later(90,file=log,repeat=False)
        CENTER.install()
        try:
            preload_compatibility_imports()
            from tools.rvc_modern_qt import sg,startup_progress,_app,configure_branding,STYLESHEET
            from tools import rvc_i18n
            app=_app()
            configure_branding(app)
            app.setStyleSheet(STYLESHEET)
            rvc_i18n.install(app)
            if not consent_valid():
                from tools.rvc_beta_ui import TermsDialog
                from PySide6.QtWidgets import QDialog
                dialog=TermsDialog(require_acceptance=True)
                try:accepted=dialog.exec()==QDialog.DialogCode.Accepted
                finally:dialog.deleteLater()
                if not accepted:return
            session,previous_unclean=begin_session()
            if previous_unclean:
                sg.popup(rvc_i18n.t('Önceki oturumun düzgün kapanması kaydedilmemiş. Günlüklerden hata ayrıntılarını kontrol edebilirsin.'))
            startup_progress('Arayüz hazır. Ses motoru yükleniyor...')
            if not SOURCE.is_file():raise FileNotFoundError(f'RVC bulunamadı: {SOURCE}')
            import tools.rvc_profiles_addon as addon
            addon.sg=sg
            from tools.rvc_model_library import install_profile_hooks
            install_profile_hooks(addon.ProfileSupport,sg)
            code=compile(prepare_source(SOURCE.read_text(encoding='utf-8-sig')),str(SOURCE),'exec')
            scope={'__name__':'__main__','__file__':str(SOURCE),'__package__':None}
            exec(code,scope)
            clean=True
        except SystemExit as exc:
            clean=exc.code in (None,0)
            raise
        except Exception as exc:
            CENTER.report(exc,'Uygulamayı başlatma')
            traceback.print_exc()
            try:
                from tools.rvc_modern_qt import sg,finish_startup
                from tools.rvc_i18n import t
                finish_startup()
                sg.popup_error(t('RVC Studio açılamadı. Hata kaydı: ')+str(logs/'modern_startup.log')+'\n\n'+str(exc))
            except Exception:
                if sys.platform=='win32':
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None,'RVC Studio could not start.\n'+str(exc)+'\n'+str(logs/'modern_startup.log'),'RVC Studio',0x10)
        finally:
            if scope and 'gui' in scope:
                try:scope['gui'].stop_stream()
                except Exception as exc:CENTER.report(exc,'Ses akışını durdurma')
            if session and clean:
                try:end_session(session)
                except Exception as exc:CENTER.report(exc,'Oturumu kaydetme')
            CENTER.uninstall()
            faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
            sys.stdout,sys.stderr=stdout,stderr


if __name__ == '__main__':
    if '--check' in sys.argv:
        preload_compatibility_imports()
        prepare_source(SOURCE.read_text(encoding='utf-8-sig'))
        from tools import rvc_modern_qt, rvc_monitor, rvc_studio_tools, rvc_hotkeys, rvc_diagnostics, rvc_gpu, rvc_model_database, rvc_model_library, rvc_media_ui, rvc_soundpad, rvc_file_convert, rvc_link_download, rvc_audio_quality, rvc_audio_setup, rvc_wave_edit, rvc_clip_editor, rvc_user_state, rvc_errors, rvc_profile_backup, rvc_i18n, rvc_settings_ui, rvc_beta_ui
        from PySide6.QtGui import QImageReader
        for name in ('rvc_logo.png', 'rvc_logo.ico', 'skymoon_avatar.png', 'skymoon_banner.png'):
            image = QImageReader(str(ROOT / 'assets' / name))
            if not image.canRead() or image.read().isNull():
                raise RuntimeError('Uygulama görseli okunamıyor: ' + name)
        from tools.rvc_noise import NativeRNNoise
        NativeRNNoise().close()
        import av, yt_dlp, yt_dlp_ejs
        if not (ROOT/'assets'/'downloader'/'deno.exe').is_file():
            raise RuntimeError('Soundpad indirme çalışma zamanı eksik.')
        # Optional repair must be usable without downloading/loading its model.
        import json
        from tools.rvc_enhance import SpeechRepair
        from tools.rvc_apbwe import RealtimeAPBWE
        from tools.rvc_apbwe_runtime import APBWERuntime
        from tools.resemble_runtime.enhancer.enhancer import Enhancer
        from tools.resemble_runtime.enhancer.hparams import HParams
        params = json.loads((ROOT/'assets'/'enhance'/'hparams.json').read_text(encoding='utf-8'))
        HParams(**params)
        if params.get('wav_rate') != 44100 or not (ROOT/'tools'/'rvc_enhance_worker.py').is_file():
            raise RuntimeError('Deneysel ses onarımı dosyaları eksik.')
        if not (ROOT/'assets'/'apbwe'/'config.json').is_file():
            raise RuntimeError('Canlı AP-BWE yapılandırması eksik.')
        print('RVC Studio 1.3.0 Beta 1: arayüz, profiller, diller ve ses bileşenleri kontrolü başarılı.')
    else:
        try:
            main()
        except Exception as exc:
            # Failures before logging/Qt initialization still get a visible message.
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(None,
                    'RVC Studio could not start / RVC Studio açılamadı.\n\n'+str(exc),
                    'RVC Studio',0x10)
            else:
                print('RVC Studio could not start:',exc,file=sys.stderr)
