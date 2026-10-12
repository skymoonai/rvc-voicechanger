<div align="center">

# RVC Studio

**Windows için gerçek zamanlı yapay zekâ ses dönüştürme ve Soundpad uygulaması**  
*Real-time AI voice conversion and soundboard for Windows*

[![Release](https://img.shields.io/github/v/release/skymoonai/rvc-voicechanger?include_prereleases&label=beta%20release)](https://github.com/skymoonai/rvc-voicechanger/releases)
![Windows](https://img.shields.io/badge/Windows-10%2F11%20x64-blue)
![Beta](https://img.shields.io/badge/status-public%20beta-orange)

[**⬇️ İndir / Download**](https://github.com/skymoonai/rvc-voicechanger/releases) · [**🐛 Hata bildir / Report a bug**](https://github.com/skymoonai/rvc-voicechanger/issues/new/choose) · [**English**](#english)

</div>

> [!WARNING]
> **Beta sürüm:** RVC Studio geliştirme aşamasındadır. Hatalar, çökmeler, ses kesintileri veya bazı cihazlarla uyumsuzluklar olabilir. Önemli seslerinizi ve profillerinizi yedekleyin. Proje ücretsiz ve kâr amacı gütmeyen bir girişim olarak geliştirilmektedir.

## 🇹🇷 Türkçe

### Nedir?

RVC Studio, RVC tabanlı ses modelleriyle **mikrofon sesini gerçek zamanlı dönüştürmek**, ses dosyalarını modele dönüştürmek ve **Soundpad** üzerinden klipler çalmak için geliştirilen Windows masaüstü uygulamasıdır. Türkçe ve İngilizce arayüzü vardır.

### Özellikler

| Özellik | Açıklama |
|---|---|
| 🎙️ Canlı RVC | Seçili ses modeliyle gerçek zamanlı mikrofon dönüştürme |
| 🔊 Soundpad | Klip çalma/düzenleme, kısayollar, mikrofona ve kulaklığa ayrı ses düzeyi |
| 🎼 Dosyadan dönüşüm | MP3/WAV dosyalarını ses modeliyle dönüştürme, WAV çıktısı |
| 📚 Model kütüphanesi | Ses modellerini ve modele özel ayarları yönetme |
| 📦 Taşınabilir profiller | Modellerin/ayarların ve Soundpad seslerinin içe/dışa aktarımı |
| 🖥️ Donanım seçenekleri | CPU, NVIDIA CUDA; AMD/Intel DirectML (deneysel) |
| 🌐 Arayüz dilleri | Türkçe ve İngilizce |
| 🔄 Güncelleme ve geri alma | İmzalı paketler, SHA-256 doğrulaması, son güncellemeyi geri alma |
| 🎚️ Ses araçları | Gürültü azaltma ve ek ses işleme seçenekleri |

### İndirme ve kurulum

1. [**GitHub Releases**](https://github.com/skymoonai/rvc-voicechanger/releases) sayfasından uygun **beta** `RVC_Studio_Setup_*.exe` dosyasını indirin.
2. **Windows 10/11 64-bit** üzerinde kurucuyu çalıştırın ve kurulum dizinini seçin.
3. İşlemci veya ekran kartı seçin: **CPU**, **NVIDIA RTX 40 (CUDA 11.8)**, **NVIDIA RTX 50 (CUDA 12.8)** ya da **AMD/Intel DirectML (deneysel)**.
4. Kurulum Python ve gerekli paketleri internetten indirir; aktif bağlantı ve yeterli disk alanı gerekir.
5. Mikrofon, kulaklık ve gerekiyorsa sanal ses kablosunu seçip kullanma hakkına sahip olduğunuz RVC modelini ekleyin.

> **Sanal kablo:** VAC Lite ayrı bir üçüncü taraf ürünüdür ve kendi lisansına tabidir; kişisel ev kullanımı koşullarını okuyun. Var olan başka bir sanal ses kablosunu da seçebilirsiniz.

### Güncelleme ve geri alma

**Ayarlar → Güncelleme ve Geri Alma** bölümünden Beta/Stable kanalını, güncelleme kontrolünü ve geri almayı kullanabilirsiniz. Uygulama indirilen güncellemelerde **Ed25519 imzasını** ve dosya bütünlüğünü doğrular. Kod odaklı güncellemeler ses modellerinizi, Soundpad kitaplığınızı ve profillerinizi değiştirmemek üzere tasarlanmıştır. Önemli bağımlılık/sürücü güncellemeleri tam kurucu gerektirebilir.

### Hata bildirme

[**GitHub Issues**](https://github.com/skymoonai/rvc-voicechanger/issues/new/choose) üzerinden uygulama sürümünü, Windows ve CPU/GPU bilgilerini, hatanın nasıl tekrarlandığını ve hassas bilgilerden temizlenmiş hata mesajını paylaşın. **Özel ses dosyaları, model ağırlıkları, anahtarlar veya kişisel dosya yollarını public issue'lara yüklemeyin.**

### Sorumlu kullanım ve lisans

- Başkasının sesini kullanmadan önce **izin alın**. Taklit, aldatma, dolandırıcılık veya izinsiz kimliğe bürünme amacıyla kullanmayın.
- İndirdiğiniz ses ve modellerin lisans/telif haklarına uyun; güvenmediğiniz `.pth` modellerini yüklemeyin.
- Yazılım **olduğu gibi** sağlanır. Bir README veya kullanıcı sözleşmesi, kullanıcının hukuki sorumluluklarını otomatik kaldırmaz.
- Depodaki **MIT lisansı**, üçüncü taraf bileşen ve model ağırlıklarına kendiliğinden hak vermez. Projenin kâr amacı gütmemesi MIT'nin izinlerini değiştirmez.

---

## English

RVC Studio is a **Windows desktop app for real-time RVC voice conversion, offline file conversion, and Soundpad playback**. It includes Turkish/English UI, portable model and Soundpad profiles, CPU/NVIDIA CUDA backends, experimental AMD/Intel DirectML support, and signed software updates with rollback.

### Install

Download the latest applicable **beta** `RVC_Studio_Setup_*.exe` from [GitHub Releases](https://github.com/skymoonai/rvc-voicechanger/releases). Run on **Windows 10/11 x64**, choose an installation directory and hardware backend, and allow the installer to download Python dependencies. Use only voice models you are authorized to use.

### Updating

Open **Settings → Updates and Rollback** to check for cryptographically verified updates or roll back the most recent supported code update. Models, Soundpad clips, profiles and Python environments are excluded from normal code-only patches. A full installer may be required for major dependencies.

### Safety

**Beta software, provided as-is.** Bugs, crashes, audio glitches and incompatibilities are possible. Back up important data. Never use voice conversion for impersonation, fraud or deception. Respect the rights of voice owners and third-party software/model licenses. Report sensitive vulnerabilities privately using [SECURITY.md](SECURITY.md).

**Publisher:** [skymoonai](https://github.com/skymoonai) · [Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Releases](https://github.com/skymoonai/rvc-voicechanger/releases)
