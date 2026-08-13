# systemd birimleri

- **Çekirdek servisi** — FastAPI sidecar'ı servis olarak çalıştırır.
- **Ayrıcalıklı tarama birimi** — tam sistem antivirüs taraması kök yetkisi
  ister; uygulama normal kullanıcı olarak çalıştığı için taramayı doğrudan
  yapamaz. Uygulama bu birimi tetikler, sonucunu okur (ADR 0009).

  Kural: erişilemeyen yollar tarama raporunda **açıkça listelenir.** Atlanan
  yol varken tarama "temiz" olarak raporlanmaz — eksik tarama, yanıltıcı
  temiz raporundan iyidir.

Birim dosyaları kod aşamasında yazılacak.
