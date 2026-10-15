# RVC Studio 1.5.2 Beta 1 — AMD Motoru eklentisi

**Bu, v1.5.2'nin kendi PySide6 arayüzünü kullanan deneysel entegrasyondur.**
`tools/rvc_modern_qt.py` dosyasını AMD geliştirme dalından KOPYALAMAYIN.
Bu dalın ana kaynak ağacı 1.3 tabanlıdır; 1.5.2'nin güncel arayüz
kaynakları GitHub deposunda yayımlanmamıştır. Eski dosyayı kopyalamak,
`'Window' object has no attribute 'tray'` hatasına yol açar.

## Kurulum

1. 1.5.2 kurucusunu tekrar `C:\RVCStudio` konumuna yükleyerek orijinal
   arayüzü geri getirin. Önce kişisel modelleri/profilleri yedekleyin.
2. [AMD geliştirme dalını](https://github.com/skymoonai/rvc-voicechanger/tree/feature/amd-vcrs-preview)
   GitHub'da **Code → Download ZIP** ile indirin ve örnek olarak
   `%USERPROFILE%\Downloads\rvc-voicechanger-feature-amd-vcrs-preview`
   klasörüne çıkartın.
3. Windows PowerShell'de **aşağıdaki kodu**, çıkartma yolunuz farklıysa
   `$source` değişkenini düzenleyerek çalıştırın:

```powershell
$source = "$env:USERPROFILE\Downloads\rvc-voicechanger-feature-amd-vcrs-preview"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$source\scripts\install_amd_ui_extension.ps1" -Target "C:\RVCStudio" -SourceRoot $source
```

Eklenti kurucusu sadece şu değişiklikleri yapar:

- Mevcut `tools/rvc_modern_qt.py` için, kullanıcı AppData klasörüne yedek alır.
- `tools/rvc_amd_bridge.py` ve `tools/rvc_amd_extension.py` dosyalarını
  kopyalar.
- Orijinal arayüzün **sonuna** güvenli yükleme bloğu ekler. Tray, ses
  callback'i, update akışı ve diğer orijinal metodlar değişmez.
- Python kodunun sözdizimini kontrol eder, başarısızsa otomatik geri alır.
- **1.5.2** sürüm denetimi ve tray önkontrolü yapar. Sürüm uyuşmazlığında
  değişiklik yapmaz.

4. Rust motoru `C:\RVCStudio\engines\vc-rs\vc-rs.exe` altında mevcut olmalı.
   Yoksa `scripts/install_vcrs_amd.ps1` ile yeniden kurun.
5. RVC Studio'yu açın, **AMD Motoru** sekmesini seçin; RVC v2/F0 ONNX
   modelini, ContentVec/RMVPE ONNX destek modellerini, WASAPI giriş/çıkışını
   ve **DirectML** sağlayıcısını seçin. İlk deneme için **200 veya 500 ms**.
6. Ana Python ses akışı duruyorken **AMD Ses Motorunu Başlat** düğmesini kullanın.

**Sınırlamalar:** Bu prototip, AMD için Rust motorunu farklı bir WASAPI
işleminde çalıştırır. INDEX, Soundpad çıkış yönlendirmesi, formant,
A/B, RVC Studio içi canlı seviye monitörü ve canlı ayar değiştirme henüz
bağlı değildir. Sanal makinede gerçek AMD GPU çıkarımı doğrulanmalıdır.

## Geri alma

```powershell
$source = "$env:USERPROFILE\Downloads\rvc-voicechanger-feature-amd-vcrs-preview"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$source\scripts\install_amd_ui_extension.ps1" -Target "C:\RVCStudio" -Remove
```

Bu komut yalnızca AMD başlangıç bağlantısını kaldırır; orijinal arayüzü
başka bir sürümle değiştirmez. AMD Rust ikililerini silmez.

## Geliştirici notları

- Windows UI açıldıktan sonra `install(Window)`, orijinal
  `Window.__init__` / `read` / `shutdown` metodlarını sarmalar.
  Orijinal kurucunun çalışması ve sistem tepsisinin oluşturulması önce
  tamamlanır. AMD eklentisi yüklenemezse stok pencere çalışmaya devam eder.
- `Window.read`, AMD aktifken normal RVC başlatma isteğini reddeder.
  AMD başlangıç düğmesi de stok stream aktifken motor başlatmayı reddeder.
- CI: sahte bir 1.5.2 pencere üzerinde ekleme, yedekleme,
  tekrar kurma ve kaldırma; ayrıca Window.tray koruma ve bağımsız
  subprocess mantığı test edilir.
- **Sınır:** GitHub deposunda 1.5.2'nin birebir UI kaynakları olmadığı
  için gerçek 1.5.2 kurucusuyla tam uygulama testi yapılamadı. Kurulum betiği
  sürüm önkontrolünü geçmezse **zorlamayın**; yüklü
  `tools/rvc_user_state.py` içindeki `APP_VERSION` değerini ve
  `tools/rvc_modern_qt.py` dosyasında tray olup olmadığını kontrol edin.
