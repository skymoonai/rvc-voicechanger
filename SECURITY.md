# RVC Studio — Security Policy / Güvenlik Politikası

RVC Studio şu anda **public beta** durumundadır. Güvenlik düzeltmeleri öncelikle güncel beta için sağlanır; eski beta sürümleri desteklenmeyebilir.

## Özel bildirim / Private reporting

**Güvenlik açıklarını, özel anahtarları, token'ları veya kişisel sesleri GitHub Issues'ta herkese açık paylaşmayın.**

GitHub deposundaki **Security → Report a vulnerability** seçeneği etkinse kullanın; etkin değilse depo sahibiyle özel ve güvenilir bir kanaldan iletişime geçin.

**Do not publish exploitable vulnerabilities, private keys, tokens or private audio in public issues.** Use GitHub Private Vulnerability Reporting where available, otherwise reach the maintainer privately.

## Updates / Güncellemeler

The updater verifies HTTPS certificates, Ed25519-signed release metadata and packages, and SHA-256 hashes. It does not intentionally bypass TLS certificate checks. The publisher's private signing key must **never** be included in the app, setup file, repository or public release archive.

## Model safety / Model güvenliği

Only load voice models from trusted sources and with appropriate permission. Untrusted serialized model formats such as `.pth` may present security risks. Respect third-party model and software licenses. Remove personal paths, keys, recordings and other sensitive information from bug reports.
