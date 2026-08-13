# Siparişler

Mağazanın günlük çalışma ekranı: sipariş listesi, süzgeçler, durum akışı ve
sipariş üzerinden fatura/kargo/iade eylemleri.

**Durum:** iskelet. `module.yaml` sözleşmesi sabit, kod yazılmadı
(`enabled: false`).

## Neden bu modül sonradan eklendi

Yan menüdeki diğer 19 ekran siparişin *çevresini* yönetiyordu — Kargo, Fatura,
İadeler, Sanal POS — ama siparişin kendisini yöneten bir yer yoktu. "Bugün
hangi siparişler geldi, hangileri kargolanmayı bekliyor" sorusunun tek bir
cevabı olmuyordu. Menüde Kontrol Paneli'nin hemen ardına girer (`order: 15`);
**mevcut ekranların sırası değişmez.**

## Sınırlar

Bu ekran siparişin kendisini yönetir. Şunlar başka modüllerin işidir ve
buradan yalnız *tetiklenir*, burada *uygulanmaz*:

| İş | Sahibi |
|---|---|
| Gönderi oluşturma, etiket satın alma, takip | `store_shipping` |
| Fatura kesme ve PDF | `store_invoices` |
| İade akışı ve para iadesi | `store_refunds` |
| Müşteri kaydı ve adresleri | `store_customers` |
| Ürün, fiyat, stok | `store_products` |

Sipariş detayındaki sekmeler bu modüllerin ilan ettiği yeteneklerden beslenir
(`consumes`, K3). İlgili modül kapalıysa o sekme **gizlenir**; ekran çalışmaya
devam eder (K7).

Grup: **BBD Store** · İzinler: `store_orders.view`, `store_orders.manage`,
`store_orders.cancel`
