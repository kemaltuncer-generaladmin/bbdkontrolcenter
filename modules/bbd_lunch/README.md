# Öğle Yemeği modülü

Kantinde tek tek QR okutularak girilen öğle yemeğini, takvimden gün seçip toplu
işler. Yazma kantinin **kendi satış ucundan** geçer — sonuç kasada elle
girilmişten ayırt edilemez: aynı işlem satırı, aynı cari borç, aynı stok düşümü.

## Ne yapar

- **Takvim.** Ay görünümü; her günün hücresinde işlenen porsiyon sayısı, hatalı
  satır uyarısı ve tatil işareti. Sağ tık tatil işaretler/kaldırır.
- **Toplu seçim.** Sınıfa göre gruplu liste (sınıf bilgisi Öğrenci Yönetimi
  modülünden `bbd_students.list` yeteneğiyle gelir), sabit liste, önceki günün
  listesini kopyalama.
- **Ön izleme.** Gönderimden önce her öğrenci için engel, harcama limiti, stok
  yeterliliği ve "bu gün zaten girilmiş mi" yanıtlanır.
- **Aralığa işleme.** Seçili listeyi bir tarih aralığının iş günlerine işler;
  hafta sonu ve tatil günleri atlanır.
- **Geçmiş ve geri alma.** Parti ya da tek öğrenci geri alınabilir.

## Veri güvenliği

- **Çift borç imkânsız.** `local_id` (gün + öğrenci + deneme sırası) üzerinden
  deterministik üretilir ve **gönderimden önce** kendi tablomuza yazılır. Ağ
  koparsa aynı id ile tekrar gönderilir; kantin `duplicate` der.
- **O gün yemeği zaten işlenmiş öğrenci varsayılan olarak atlanır.** İkinci
  porsiyon ancak açık onayla (`allowRepeat`) girilir.
- **Geri alma silme değildir.** Kantinde ters cari kayıt yazılır, stok iade
  edilir, işlem "iptal" damgalanır. Ne kantinde ne burada satır silinir.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_lunch.view`, `bbd_lunch.manage`, `bbd_lunch.reverse`
