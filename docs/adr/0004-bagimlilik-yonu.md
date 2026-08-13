# 0004 — Bağımlılık yönü: çekirdek modülü bilmez

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Modüler mimarilerin en sık çöküş nedeni, çekirdeğin zamanla modüllere sızmasıdır
(modüle özel `if`, doğrudan import, "geçici" özel durum).

## Karar
Bağımlılık tek yönlüdür ve denetlenir:

- `km_core` / `km_platform` içinde modül adı, modül importu veya modüle özel dal
  bulunamaz.
- Modüller yalnızca `km_sdk` import eder; `km_core` ve `km_platform` doğrudan
  import edilemez.
- Modüller birbirini import edemez; yalnızca registry veya olay veri yolu
  üzerinden konuşur.

## Gerekçe
- `modules/` silindiğinde çekirdek ayağa kalkabiliyorsa mimari gerçekten
  modülerdir — bu ölçülebilir bir testtir.
- `km_sdk` ara katmanı, çekirdek içi yeniden düzenlemenin modülleri kırmasını
  önler.

## Sonuçlar
- Bu kurallar CI'da otomatik denetlenir (import linter). Yorum satırıyla değil,
  kapıyla korunur.
- Bir modülün başka modülün verisine ihtiyacı varsa çözüm import değil,
  `provides`/`consumes` yeteneği veya olaydır.
