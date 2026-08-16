# Ders Takvimi

Haftalık zil saatlerinin ve grupların **salt okunur** görünümü.

**Bu ekran hiçbir şeyin sahibi değildir.** Veri Zil Sistemi modülünündür ve
`bell.week` yeteneği üzerinden okunur (K3). Burada değiştirilebilir tek bir
alan yoktur; `bbd_class_schedule.manage` izni de bu yüzden kaldırılmıştır.

0.1'de yön tersineydi: saatlerin sahibi burasıydı, zil onları okuyordu. İki
ekranda iki ayrı doğru kaynak tutmak hangisinin geçerli olduğunu belirsiz
kıldığı için 0.2'de sahiplik Zil Sistemi'ne verildi.

- Sözleşme: [module.yaml](module.yaml) · Giriş: `backend/module.py`
- Uç nokta: `GET /api/bbd_class_schedule/week`
- Grup: **BBD** · İzin: `bbd_class_schedule.view`

`backend/migrations/001_groups.sql` yerinde durur. Uygulanmış göç geri alınmaz;
`mod_bbd_class_schedule_document` tablosu artık okunmuyor, silinmiyor.
