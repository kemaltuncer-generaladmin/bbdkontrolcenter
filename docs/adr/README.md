# Mimari Karar Kayıtları (ADR)

Her dosya bir kararı, gerekçesini ve sonuçlarını kaydeder. Karar verildikten
sonra dosya **değiştirilmez**. Karar değişirse yeni bir ADR yazılır ve eskisinin
durumu `Superseded by NNNN` yapılır.

Durum değerleri: `Kabul edildi` · `Superseded by NNNN` · `Reddedildi`

| # | Karar | Durum |
|---|---|---|
| 0001 | Çekirdek: Python 3 + FastAPI | Kabul edildi |
| 0002 | Arayüz: Tauri 2 masaüstü kabuğu | Kabul edildi |
| 0003 | Modül sistemi: manifest + dinamik keşif | Kabul edildi |
| 0004 | Bağımlılık yönü: çekirdek modülü bilmez | Kabul edildi |
| 0005 | Modül sınırı: dikey dilim | Kabul edildi |
| 0006 | ssh ve database platform yeteneğidir | Kabul edildi |
| 0007 | Kimlik ve yetkilendirme: PIN ile giriş, izin tabanlı çok rollü model | Kabul edildi |
| 0008 | Bağımlılık yönetimi ve sürücü politikası | Kabul edildi |
| 0009 | Antivirüs: ClamAV, modül olarak | Kabul edildi |
| 0010 | SMS sağlayıcı entegrasyonu: sarmalanmış Netgsm | Kabul edildi |
| 0011 | Panel arayüz kiti kabukta durur | Kabul edildi |
| 0012 | Mağaza yıkıcı işlemleri PIN yerine gerekçeli onay ister | Kabul edildi |
