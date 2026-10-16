//! Single-purpose, source-licensed vc-convert host for RVC Studio.
//! MIT-licensed vc-rs converter implementation by shirohata.
//! This file is copied into crates/vc-convert/src/bin by our Windows build CI.
use std::path::Path;
use vc_convert::{convert_pth_file, ConvertOptions};

fn main() {
    if let Err(err) = run() {
        eprintln!("HATA: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> anyhow::Result<()> {
    let file = std::env::args_os().nth(1).ok_or_else(|| {
        anyhow::anyhow!("Kullanim: rvcstudio-convert.exe C:\\models\\ses.pth")
    })?;
    let file = Path::new(&file);
    if file.extension().is_none_or(|ext| !ext.eq_ignore_ascii_case("pth")) {
        anyhow::bail!("Kaynak model .pth uzantili olmali.");
    }
    let output = file.with_extension("onnx");
    if output.exists() {
        anyhow::bail!("Hedef ONNX zaten mevcut: {}", output.display());
    }
    let path = convert_pth_file(file, &ConvertOptions::default(), &mut |stage| {
        eprintln!("{}", stage.label());
    })?;
    println!("OUTPUT={}", path.display());
    Ok(())
}
