# RVC Studio 1.5.2 — AMD Motoru (Rust / DirectML) v2

Bu eklenti, kurulu **RVC Studio 1.5.2** arayüzünün üzerine çalışır.
**ÖNEMLİ:** Geliştirme dalındaki `tools/rvc_modern_qt.py` dosyasını
kurulu programa kopyalamayın. Sistem tepsisi (`tray`) hatasına yol açar.
Güncel `install_amd_ui_extension.ps1` sadece arayüzü destekleyen küçük
bir bağlantı ekler, önce yedek alır, hatada geri döner.

## Neler eklendi?

- Normal RVC Studio'nun kartlı koyu temasıyla **AMD Ses Dönüşümü** sayfası.
- **PTH seçildiğinde**, vc-rs'nin MIT lisanslı saf Rust dönüştürücüsü
  `rvcstudio-convert.exe` aracılığıyla uygulama içinde, arka planda,
  başka GUI açılmadan ONNX'e dönüşüm. Destek: yalnızca RVC v2 / F0;
  mevcut ONNX asla zorla üzerine yazılmaz.
- **ContentVec ve RMVPE** için açılışta otomatik indirme; yaklaşık **741 MB**,
  Hugging Face `wok000/weights_gpl` GPL-3.0 ağırlıkları.
  Dosyalar `%LOCALAPPDATA%\RVCStudio\engines\vc-rs\models` altında
  tutulur. Gerçek SHA-256 ve dosya boyutu ile doğrulanır.
  Kullanıcının önceki vc-rs GUI indirmeleri doğrulanarak yeniden kullanılır.
  İnternet yoksa yeniden dene düğmesi vardır.
- **WASAPI giriş/çıkış cihazları açılır listeden** seçilir. Mevcut
  RVC Studio `Giriş ve Çıkış` ayarlarının isimleri eşleştiğinde
  AMD seçimi de takip edilir.
- **Chunk 50–1000 ms kaydırıcı** (10 ms basamaklarla uygulanır) ve
  pitch kaydırıcı.
- AMD sekmesindeyken RVC Studio'nun ana başlat/durdur komutları
  AMD motoruna yönlendirilir; iki canlı ses motoru aynı anda açılamaz.
- **Günlük:** vc-rs satırları RVC Studio'nun ortak Günlük sekmesine
  aktarılır; AMD ekranında ayrı konsol kutusu yoktur.
- **Alt gösterge paneli:** vc-rs'nin saniyelik gerçek model çıkarım
  ölçümü (çıkarım/ms), *nominal* içerik gecikmesi (yaklaşık ~ms),
  GPU Engine performans sayacıyla vc-rs subprocess GPU% (destekleniyorsa).
  **Örnekleme** değeri vc-rs CLI tarafından dışarı verilmediği için
  şimdilik `—` gösterilir; değer uydurulmaz.

### Kurulum — tek PowerShell bloğu

Önceden `tray` hatası aldıysanız, **önce resmi 1.5.2 Setup.exe'yi
`C:\RVCStudio` konumuna yeniden yükleyin**. Kullanıcı modellerini ve
profillerini yedekleyin. Mevcut `vc-rs` motoru kurulu kalabilir.

Ardından Windows PowerShell'e yalnızca aşağıdaki kod bloğunun
**içini** yapıştırın. `PS C:\...>` istemini veya çıktıları yapıştırmayın.

```powershell
$ErrorActionPreference = 'Stop'
$zip = Join-Path $env:TEMP 'rvcstudio-amd-v2.zip'
$folder = Join-Path $env:TEMP 'rvcstudio-amd-v2'
if (Test-Path $folder) { Remove-Item -Recurse -Force $folder }
Invoke-WebRequest -Uri 'https://github.com/skymoonai/rvc-voicechanger/archive/refs/heads/feature/amd-vcrs-preview.zip' -OutFile $zip -UseBasicParsing
Expand-Archive -LiteralPath $zip -DestinationPath $folder -Force
$installer = Get-ChildItem $folder -Recurse -Filter 'install_amd_ui_extension.ps1' | Select-Object -First 1
if (!$installer) { throw 'AMD kurulum dosyası ZIP içinde bulunamadı.' }
$source = Split-Path (Split-Path $installer.FullName -Parent) -Parent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$($installer.FullName)" -Target 'C:\RVCStudio' -SourceRoot "$source"
```

Kurulum otomatik olarak:
- Dosyaları yedekler, `tools/rvc_amd_*.py` modüllerini yerleştirir.
- CI tarafından derlenmiş ve **SHA-256 ile doğrulanmış** Rust PTH
  dönüştürücüsünü `C:\RVCStudio\engines\vc-rs` altına koyar.
- Orijinal 1.5.2 `Window` ve `tray` kodunu korur.
- Değişikliklerden sonra Python sözdizimini kontrol eder.

**Motorun kendisi** (`vc-rs.exe`) hâlâ kurulu olmalıdır.
Gerekiyorsa şu komutla kurun:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\RVCStudio\scripts\install_vcrs_amd.ps1' -Destination 'C:\RVCStudio\engines\vc-rs'
```

Kurulumdan sonra RVC Studio'yu açın. AMD sekmesindeki ilk indirme
tamamlanınca `Model seç` butonuyla `.pth` veya `.onnx` seçin.
Sanal makinede ses giriş/çıkışının gerçekten mevcut olması gerekir.
Chunk 200 ms'den başlayın; kesinti varsa 250–500 ms'ye yükseltin.

### Geri alma

Yukarıdaki kurulumdan sonra temp içeriği duruyorsa:

```powershell
$folder = Join-Path $env:TEMP 'rvcstudio-amd-v2'
$installer = Get-ChildItem $folder -Recurse -Filter install_amd_ui_extension.ps1 | Select-Object -First 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$($installer.FullName)" -Target 'C:\RVCStudio' -Remove
```

Bu işlem AMD arayüz bağlantısını kaldırır; ses modeli ağırlıklarını
ve Rust çalışma zamanını silmez.

### Bilinen sınırlar

**1.5.2'nin kurucudaki gerçek koduyla Windows uygulama testine henüz
erişilemedi.** CI: Python derlemesi, PS parser, sahte 1.5.2 arayüzü,
kurma/çıkarma, Rust CLI kontrolü ve küçük v2/F0 checkpoint dönüşümünü sınar.
Kullanıcı testiyle gerçek uygulama davranışı netleştirilmeli.

AMD motoru ayrı WASAPI işlemidir. Soundpad işleme, `.index` eşleştirmesi,
formant, ana uygulamanın canlı ses metreleri ve RVC profil eşitlemesi henüz
AMD motoruna bağlanmadı. Rust'un raporladığı `content_delay_ms` gerçek
mikrofondan kulaklığa gecikme değildir. AppSandbox GPU-PV'de AMD hızlandırması
ayrıca doğrulanmalıdır.
