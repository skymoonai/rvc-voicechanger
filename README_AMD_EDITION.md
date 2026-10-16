# RVC Studio AMD Edition — Rust/DirectML development preview

**AMD Edition is a separate experimental build path**, not the NVIDIA/CUDA
version and **not a public standalone setup.exe release**. Work is kept on
`feature/amd-edition`; the production `main` branch is untouched.

## What changed

The original **Ses Dönüşümü** screen now starts the Rust `vc-rs` voice
engine. There is **no AMD Motoru tab**. No older 1.3 Qt UI source replaces the
installed 1.5.2 system tray.

- PTH→ONNX runs in-app with pinned native Rust converter, only RVC v2/F0.
- ContentVec/RMVPE automatically download and verify into per-user persistent
  storage. Their upstream GPL-3.0 licence is distinct from this app's code.
- Microphone and output devices are actual WASAPI dropdowns; native vc-rs
  owns both sound streams.
- Native pitch **−24…+24 semitones**, RMS level mix and threshold dBFS;
  chunk length adjustable, changes restart Rust audio with a small pause.
- RMVPE is the available F0 estimator. FCPE and PM controls are hidden.
- The main **Başlat / Durdur** buttons, Normal Mikrofon (Rust passthrough),
  Dönüştürülmüş Ses (RVC), Mute and Gecikmeyi Sıfırla (process restart)
  are wired to AMD.
- Input and output level bars reflect **vc-rs-reported real input/output RMS**
  roughly every second. The output meter is the Rust engine's output, not a
  fake PyTorch callback. Inference milliseconds are native measurements.
  GPU% uses a **vc-rs process-PID GPU Engine PDH counter** if Windows exposes
  it, otherwise shows `—`.
- The displayed `~delay` is vc-rs's **nominal content delay**, *not
  microphone-to-speaker end-to-end latency*. Unavailable device sample-rate
  statistics are hidden.
- "Kendi Sesimi Dinle" now has an experimental independent monitor of the
  **recording half of the chosen virtual cable** into the selected headphones.
  For a real physical speaker output, do not create a duplicate loopback.
  Not all AppSandbox/VAC devices have an identifiable paired endpoint.
- Stock Soundpad's **independent mic lane** can send clips to the same Rust
  WASAPI shared output. This is **Windows mixer-based output**, not a
  sample-accurate mixer inside vc-rs; WASAPI exclusive mode will not work.
- File conversion on the original **Dosyadan Dönüştür** UI uses `vc-rs wav`,
  including batch queue, cancellation, optional native RNNoise and pre-existing
  post-conversion file repair. Input conversion uses bounded PyAV decode.
- Short test captures input, uses vc-rs WAV to render output, and stores the
  result in the original in-memory player. A/B uses the same original
  settings snapshot + playback UI, but renders with the Rust WAV engine.
- AI LAB Türkiye PTH downloads are converted to sibling ONNX in the
  background, then remain visible in the original profile library.
- **Cross-backend profiles:** original PTH and safe backup service are
  preserved, the ONNX derivative is regenerated when needed. Source-only
  ONNX models can be selected for a session but are **not yet included by the
  original PTH/index portable backup format**.

### Intentionally disabled/not supported yet

| Feature | AMD Edition behavior |
|---|---|
| FAISS .index / Index Rate | Hidden — vc-rs ONNX path has no .index reader |
| Formant / "Ses karakteri" | Hidden — no equivalent Rust formant control |
| FCPE / PM | Hidden — RMVPE only |
| AP-BWE | Disabled — Python post-filter is not inside Rust audio path |
| RNNoise strength/hold | Disabled — Rust supports on/off, not those Python DSP controls |
| MIGraphX | Not selectable without a Windows ML AMD EP; the user's `doctor` reported no supported catalog EPs |
| End-to-end audio delay | Not available; content-delay is marked approximately |
| True device sample rate | Not exported by current vc-rs CLI; the indicator is hidden |
| Rust-exact sample-mixed Soundpad | Uses Windows shared mixer, not vc-rs's ring buffer |
| Guaranteed converted-voice headphone monitor | Requires a real pairable virtual audio cable recording device |

Do not advertise these capabilities as implemented.

## Upgrade an existing RVC Studio 1.5.2 / AMD-tab test

Close RVC Studio and the standalone `vc-gui.exe` first. Back up your
`%LOCALAPPDATA%\RVCStudio` folder and model library. The installer creates
a timestamped backup of the version-matched Qt module before changing it.

Paste **only the following code** in Windows PowerShell (not the `PS C:\>`
prompt or a previous command's output):

```powershell
$ErrorActionPreference = 'Stop'
$zip = Join-Path $env:TEMP 'rvcstudio-amd-edition.zip'
$folder = Join-Path $env:TEMP 'rvcstudio-amd-edition'
if (Test-Path $folder) { Remove-Item -LiteralPath $folder -Recurse -Force }
Invoke-WebRequest -Uri 'https://github.com/skymoonai/rvc-voicechanger/archive/refs/heads/feature/amd-edition.zip' -OutFile $zip -UseBasicParsing
Expand-Archive -LiteralPath $zip -DestinationPath $folder -Force
$installer = Get-ChildItem $folder -Recurse -Filter 'install_amd_edition.ps1' | Select-Object -First 1
if (!$installer) { throw 'AMD Edition kurucusu bulunamadı.' }
$source = Split-Path (Split-Path $installer.FullName -Parent) -Parent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer.FullName -Target 'C:\RVCStudio' -SourceRoot $source
if ($LASTEXITCODE -ne 0) { throw 'AMD Edition entegrasyonu tamamlanamadı.' }
```

This updater requires installed **1.5.2 source files including tray**.
It removes the previous AMD-tab hook if present; does not overwrite the
installed version's full Qt source; copies current AMD Python modules and the
SHA-256 pinned native converter; syntax-checks, then rolls back on failure.
The Rust `engines/vc-rs/vc-rs.exe` must already be installed. If missing,
install it with `scripts/install_vcrs_amd.ps1` from this source branch.

To remove the AMD Edition startup hook:

```powershell
$installer = Get-ChildItem (Join-Path $env:TEMP 'rvcstudio-amd-edition') -Recurse -Filter 'install_amd_edition.ps1' | Select-Object -First 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer.FullName -Target 'C:\RVCStudio' -Remove
```

**Limitations of validation:** Windows CI exercises native converter and
model fixtures, Rust CLI flags, Qt offscreen smoke and synthetic 1.5.2 tray,
and PowerShell install/uninstall. It **cannot validate exact UI hooks from
the released 1.5.2 binary**, nor an actual AMD GPU/microphone in AppSandbox.
This is deliberately **not production-ready**. The stable RVC Studio app is
not modified in GitHub `main`.


## 2026-10-16 — peltek / anlaşılmaz ses kalite düzeltmesi

**Kaynakta bulunan riskler:** AMD Edition önizlemesi başlangıçta 200 ms
chunk ile `vc-rs` CLI'nin değiştirilmemiş varsayılan **85 ms crossfade**
ve **100 ms extra convert** ayarlarını kullanıyordu. Eski Python RVC
başlangıcı `extra_time=2.5 s`, `crossfade_time=50 ms` ayarlarıyla
geliyor. Bu birebir eşdeğer işlem değil; özellikle hızlı ünsüz
geçişlerinde 85/200 oranlı SOLA pencere ses bulanıklığını artırabilir.

**Düzeltmeler:**
- **Net konuşma (varsayılan):** chunk 400 ms, crossfade 40 ms,
  extra convert 400 ms.
- **Dengeli:** chunk 300 ms, crossfade 40 ms, extra 250 ms.
- **Düşük gecikme (deneysel):** chunk 200 ms, crossfade 30 ms,
  extra 150 ms.
- Kullanıcının chunk kaydırıcısı korunur. Çok küçük chunk değerinde
  crossfade ayrıca üst sınırla kısaltılır.
- Ses algılama eşiği `-60 dB` minimumunda Rust `0.0` alır ve
  otomatik sessizlik kapısı uygulanmaz. Kullanıcı daha yüksek eşik
  seçerse bunların sözcük sonlarını kesebileceği günlükte bildirilir.
- RNNoise, eski NVIDIA seçimine bakılmaksızın AMD için varsayılan
  **kapalıdır**. AMD'ye özel ayrı ayar kaydı vardır.
- Rust `input_overruns`, `output_underruns`, `output_dropped_samples`
  ve 400ms chunk için işleme süresi aşımlarını Günlük üzerinden bildirir.
- CPAL `vc-rs doctor` varsayılan ses aygıtlarını da okur. Mikrofon
  girişi ile ses çıkışı aynı VAC cihazına gidiyorsa yeniden dönüştürmeye
  beslenen ses döngüsü oluşabileceğinden çalıştırma engellenir.

Bu değişiklikler önce olası kaynakları azaltır; tek başına tüm
PTH→ONNX modellerinin sesinin NVIDIA ile eşdeğer olacağını **garanti
etmez**. Kullanılan vc-rs motorunda RVC "Protect" (sessiz ünsüz koruma)
kontrolü doğrudan bulunmuyor.

### Kaliteyi karşılaştırma sırası

1. **Mikrofon** ile **Ses çıkışı** seçimini kontrol edin. Önceki
   AppSandbox `vc-rs doctor` ikisini de `Line 1 (Virtual Audio Cable)`
   göstermişti. Bu, bir geri besleme riskidir; iki ayrı uç seçin.
2. Pitch **0**, RNNoise **kapalı**, ses algılama eşiği **-60 dB** ve
   **Net konuşma** profilinde aynı cümleyi deneyin.
3. Rust Günlük'te input_overruns/output_underruns/output_dropped_samples
   sıfır mı, `processing` chunk süresini aşıyor mu kontrol edin.
4. Aynı ONNX dosyasıyla `vc-gui.exe` (aynı model, cihaz, chunk) testi
   yapın. İki program da kötüyse model dönüştürme/temel Rust çıkarımı
   kaynaklı olabilir; yalnızca Studio kötü ise yönlendirme/ön işleme.
5. Canlı seste kötü ama `Dosyadan Dönüştür` iyi ise mikrofon-VAC,
   tampon düşmesi ve monitörde iki sesin üst üste dinlenmesini inceleyin.

Entegrasyon hâlâ beta: kullanıcıya gerçek GPU/çevrim içi ses
performansı garanti edilmez.
