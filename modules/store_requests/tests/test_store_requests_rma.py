"""Talep verisinin saf dönüşümleri — SLA aritmetiği, zincir, kalem seçimi."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from store_requests_backend import rma

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _opened(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


# ==================================================================== SLA

def test_sla_kalan_sure_saat_olarak_sayi_doner() -> None:
    # Renk tek başına anlam taşımaz: `hoursLeft` HER ZAMAN sayıdır ve ekran
    # rozetin yanına onu yazar.
    view = rma.sla_view({"status": "new", "priority": "normal", "created_at": _opened(4)},
                        now=NOW)
    assert view["hoursLeft"] == 20.0
    assert view["state"] == "ok"
    assert "20,0 saat kaldı" == view["label"]


def test_suresi_gecen_talep_negatif_saat_ve_gecikti_yazisi_verir() -> None:
    view = rma.sla_view({"status": "reviewing", "priority": "urgent", "created_at": _opened(10)},
                        now=NOW)
    assert view["hoursLeft"] == -6.0
    assert view["state"] == "overdue"
    assert "gecikti" in view["label"]


def test_musteri_beklenirken_sayac_durur() -> None:
    # Yanıt bizde değilken geçen süreyi kendi gecikmemiz saymak, personeli
    # olmayan bir suçla cezalandırırdı.
    view = rma.sla_view({"status": "waiting_customer", "priority": "urgent",
                         "created_at": _opened(48)}, now=NOW)
    assert view["state"] == "paused"
    assert view["tone"] == "info"
    assert "sayaç durdu" in view["label"]


def test_kapanan_talebin_kalan_suresi_sifir_degil_yoktur() -> None:
    # Sıfır "tam zamanında" demektir ve kapanmış talep listesini kırmızıya
    # boyardı; doğru cevap "yok".
    for status in ("approved", "rejected", "closed"):
        view = rma.sla_view({"status": status, "priority": "urgent",
                             "created_at": _opened(500)}, now=NOW)
        assert view["hoursLeft"] is None
        assert view["state"] == "done"


def test_uzak_kayittaki_due_at_onceliğe_gore_hesabi_ezer() -> None:
    view = rma.sla_view({"status": "new", "priority": "low",
                         "created_at": _opened(1),
                         "due_at": (NOW + timedelta(hours=2)).isoformat()}, now=NOW)
    assert view["hoursLeft"] == 2.0


def test_saat_dilimsiz_damga_magazanin_yerel_saatidir_utc_degil() -> None:
    # KANIT (2026-08-13, canlı mağaza): `/api/admin/orders` en yeni siparişi
    # `2026-08-13 18:27:17` damgasıyla döndü; sunucunun kendi saati aynı anda
    # `16:44 UTC` idi. Çıplak damga UTC sayılsaydı VAR OLAN bir sipariş 1 saat
    # 43 dakika geleceğe düşerdi. Damga mağazanın yerel saatidir.
    parsed = rma.parse_time("2026-08-13 08:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None                     # aritmetik aware ister
    assert parsed == datetime(2026, 8, 13, 8, 0).astimezone()


def test_saat_dilimli_damgaya_dokunulmaz() -> None:
    assert rma.parse_time("2026-08-13T08:00:00Z") == datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
    assert rma.parse_time("2026-08-13T08:00:00+03:00") == datetime(2026, 8, 13, 5, 0, tzinfo=UTC)


def test_sla_yerel_damgadan_hesaplanir_saat_dilimi_kadar_kaymaz() -> None:
    # Üç saat ileri kaydırılmış bir açılış, 4 saatlik acil talepte "3,9 saat
    # kaldı" yazarken gerçekte 0,9 saat kalmış olması demekti: ekran tam da
    # uyarması gereken anda sakin görünürdü.
    acilis = datetime(2026, 8, 13, 10, 0).astimezone()
    view = rma.sla_view({"status": "new", "priority": "urgent",
                         "created_at": "2026-08-13 10:00:00"},
                        hours={"urgent": 4}, now=acilis + timedelta(hours=3))
    assert view["hoursLeft"] == 1.0
    assert view["state"] == "today"


def test_acilis_zamani_yoksa_sure_uydurulmaz() -> None:
    view = rma.sla_view({"status": "new", "priority": "normal"}, now=NOW)
    assert view["hoursLeft"] is None
    assert view["state"] == "none"


def test_sla_saatleri_ayardan_okunur_ve_sinirlanir() -> None:
    hours = rma.sla_hours({"urgent": 1, "high": "6", "normal": 9999, "bilinmeyen": 3})
    assert hours["urgent"] == 1
    assert hours["high"] == 6
    assert hours["normal"] == 720          # tavan
    assert hours["low"] == 48              # varsayılan korunur
    assert "bilinmeyen" not in hours


# ============================================================== liste satırı

def test_alan_adi_farkli_gelirse_de_okunur() -> None:
    # `bbd/return-requests` hâlâ yazılıyor; tek ada bağlanmak, uç yayınlandığı
    # gün boş sütun demekti.
    row = rma.request_row({"id": 7, "orderId": 42, "customerName": "Veli",
                           "type": "exchange", "status": "reviewing",
                           "created_at": _opened(2)}, now=NOW)
    assert row["orderId"] == 42
    assert row["customerName"] == "Veli"
    assert row["typeLabel"] == "Değişim"
    assert row["statusLabel"] == "İnceleniyor"


def test_talep_no_yoksa_kimlikten_turetilir_uydurulmaz() -> None:
    row = rma.request_row({"id": 12, "created_at": _opened(1)}, now=NOW)
    assert row["code"] == "#12"
    assert row["subject"] == "(konusuz)"


def test_son_mesaj_musteriden_ise_yanit_bizde_bekler() -> None:
    row = rma.request_row({"id": 1, "last_message_from": "customer",
                           "created_at": _opened(1)}, now=NOW)
    assert row["awaitingUs"] is True


# ======================================================================= pano

def test_pano_sutun_basligindaki_sayi_gercek_toplamdir() -> None:
    rows = [rma.request_row({"id": 1, "status": "new", "created_at": _opened(1)}, now=NOW)]
    columns = rma.board_columns(rows, {"new": 143})
    first = columns[0]
    assert first["key"] == "new"
    assert first["total"] == 143          # gerçek toplam
    assert first["shown"] == 1            # ekrandaki kart
    assert [column["key"] for column in columns] == list(rma.STATUS_ORDER)


# ============================================================ yazışma zinciri

def test_ic_not_yerel_isaretlenir_ve_zincire_zamanina_gore_girer() -> None:
    raw = {"messages": [
        {"id": 1, "author_type": "customer", "body": "Ürün bozuk",
         "created_at": "2026-08-10T09:00:00Z"},
        {"id": 2, "author_type": "staff", "body": "Kargoyu sorduk",
         "created_at": "2026-08-12T09:00:00Z"},
    ]}
    notes = [{"id": 5, "body": "Müşteri ikinci kez arıyor", "actor": "Ayşe",
              "created_at": "2026-08-11T09:00:00Z"}]
    thread = rma.thread_rows(raw, notes)
    assert [item["side"] for item in thread] == ["customer", "internal", "staff"]
    assert thread[1]["local"] is True
    assert thread[0]["local"] is False


def test_yanit_bizde_mi_zincirden_okunur_ic_not_sayilmaz() -> None:
    thread = [
        {"side": "customer", "body": "a"},
        {"side": "internal", "body": "b"},
    ]
    assert rma.awaiting_us(thread, "reviewing") is True
    assert rma.awaiting_us([*thread, {"side": "staff", "body": "c"}], "reviewing") is False


def test_bos_zincirde_yanit_durumu_bilinmez_hayir_denmez() -> None:
    assert rma.awaiting_us([], "new") is None


def test_kapanmis_talepte_yanit_beklenmez() -> None:
    assert rma.awaiting_us([{"side": "customer", "body": "a"}], "closed") is False


# ======================================================== iade edilecek kalem

ORDER = {"items": [
    {"id": 11, "sku": "KLM-1", "name": "Kalem", "qty_ordered": 3, "qty_refunded": 1,
     "price": "10.50"},
    {"id": 12, "sku": "DFT-2", "name": "Defter", "qty_ordered": 1, "price": "25.00"},
]}


def test_daha_once_iade_edilen_adet_dusulur() -> None:
    rows = rma.return_item_rows(ORDER, {"items": []})
    assert rows[0]["maxQty"] == 2          # 3 sipariş - 1 iade
    assert rows[0]["unitPrice"] == 1050    # kuruş, float kullanılmadan
    assert rows[1]["maxQty"] == 1


def test_talepteki_secim_kaleme_yazilir_ama_tavani_asamaz() -> None:
    rows = rma.return_item_rows(ORDER, {"items": [{"order_item_id": 11, "qty": 9}]})
    assert rows[0]["qty"] == 2             # istenen 9, iade edilebilir 2


def test_secim_siparis_adedini_asarsa_yazilmadan_once_reddedilir() -> None:
    rows = rma.return_item_rows(ORDER, {"items": []})
    assert rma.selection_error(rows, {11: 2}) == ""
    assert "en çok 2 adet" in rma.selection_error(rows, {11: 3})
    assert "bu siparişte yok" in rma.selection_error(rows, {99: 1})
    assert "negatif" in rma.selection_error(rows, {11: -1})


def test_iade_tahmini_kalem_toplamidir_ve_soz_vermez() -> None:
    rows = rma.return_item_rows(ORDER, {"items": []})
    estimate = rma.refund_estimate(rows, {11: 2, 12: 1})
    assert estimate["amount"] == 2 * 1050 + 2500
    assert estimate["items"] == 3
    assert "Kargo" in estimate["note"]


# ================================================================== süzgeçler

def test_bos_suzgec_hic_gonderilmez() -> None:
    # Laravel boş parametreyi bazen "eşittir boş" sayıyor ve liste sessizce
    # boşalıyor.
    assert rma.list_filters() == {}
    filters = rma.list_filters(q="kalem", status="new", start="2026-08-01")
    assert filters == {"q": "kalem", "status": "new", "date_from": "2026-08-01",
                       "date_field": "created"}


def test_turetilmis_suzgec_uzakta_uygulanmadiysa_sayfada_daraltilir() -> None:
    rows = [
        rma.request_row({"id": 1, "status": "new", "priority": "urgent",
                         "created_at": _opened(20)}, now=NOW),
        rma.request_row({"id": 2, "status": "new", "priority": "low",
                         "created_at": _opened(1)}, now=NOW),
    ]
    narrowed_rows, narrowed = rma.apply_local_guards(rows, sla="overdue")
    assert [row["id"] for row in narrowed_rows] == [1]
    assert narrowed is True


def test_suzgec_yoksa_liste_dokunulmadan_gecer() -> None:
    rows = [rma.request_row({"id": 1, "created_at": _opened(1)}, now=NOW)]
    same, narrowed = rma.apply_local_guards(rows)
    assert same is rows
    assert narrowed is False


# ======================================================================= para

def test_ondalik_para_kurusa_float_kullanilmadan_cevrilir() -> None:
    assert rma.to_kurus("1234.35") == 123435
    assert rma.to_kurus("1.250,00") == 125000
    assert rma.to_kurus("") is None
    assert rma.to_kurus(None) is None


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert rma.reason_error("kısa") != ""
    assert rma.reason_error("müşteri ürünü kullanmamış") == ""


def test_csv_satirinda_sla_saati_sayi_olarak_yer_alir() -> None:
    rows = [rma.request_row({"id": 3, "status": "new", "priority": "normal",
                             "created_at": _opened(4)}, now=NOW)]
    headers, table = rma.csv_table(rows)
    assert "SLA kalan (saat)" in headers
    assert table[0][headers.index("SLA kalan (saat)")] == "20,0"


# ================================ canlı mağaza alan adları (camelCase regresyonu)

#: Canlı `GET /api/admin/orders/{id}` yanıtının alan adları (2026-08-13'te
#: mağazadan okundu). Bagisto yönetici API'si camelCase üretiyor.
CANLI_SIPARIS = {
    "id": 19, "incrementId": "19", "status": "processing", "statusLabel": "İşleniyor",
    "grandTotal": 2, "baseGrandTotal": 2, "createdAt": "2026-08-13 18:27:17",
    "shippingTitle": "Hepsijet - Hepsijet", "shippingMethod": "hepsijet_hepsijet",
    "items": [{"id": 20, "sku": "123456789123456789", "name": "TEST ÜRÜNÜDÜR",
               "productId": 1428, "qtyOrdered": 1, "qtyShipped": 0, "qtyInvoiced": 1,
               "qtyCanceled": 0, "qtyRefunded": 0, "price": 2, "basePrice": 2}],
}


def test_siparis_ozeti_canli_camelcase_alanlardan_okunur() -> None:
    # Yalnız `increment_id`/`grand_total` aranırsa kart açılır ama Tarih,
    # Tutar ve Kargo satırları "—" kalır — sessiz, sinsi hata.
    ozet = rma.order_summary(CANLI_SIPARIS)
    assert ozet["number"] == "19"
    assert ozet["total"] == 200                    # 2,00 ₺ → kuruş
    assert ozet["createdAt"].startswith("2026-08-13")
    assert ozet["statusLabel"] == "İşleniyor"
    assert ozet["shipping"] == "Hepsijet - Hepsijet"


def test_siparis_ozeti_snake_case_yazimda_da_calisir() -> None:
    ozet = rma.order_summary({"id": 7, "increment_id": "SIP-7", "grand_total": "12,50",
                              "created_at": "2026-08-01T09:00:00Z", "status": "pending",
                              "shipping_method": "aras"})
    assert ozet["number"] == "SIP-7"
    assert ozet["total"] == 1250
    assert ozet["shipping"] == "aras"


def test_siparis_yoksa_ozet_uydurulmaz() -> None:
    assert rma.order_summary({}) == {}
    assert rma.order_summary(None) == {}


def test_talep_no_ve_siparis_no_camelcase_gelirse_de_okunur() -> None:
    row = rma.request_row({"requestId": 8, "requestType": "exchange", "incrementId": "SIP-9",
                           "customerEmail": "a@b.com", "assigneeId": 4,
                           "lastActivityAt": "2026-08-12T10:00:00Z",
                           "returnTracking": "RT-3", "status": "new"}, now=NOW)
    assert row["id"] == 8
    assert row["type"] == "exchange"
    assert row["orderNumber"] == "SIP-9"
    assert row["customerEmail"] == "a@b.com"
    assert row["assigneeId"] == 4
    assert row["returnCode"] == "RT-3"
    assert row["updatedAt"].startswith("2026-08-12")


def test_iptal_edilmis_kalem_iade_edilebilir_sayilmaz() -> None:
    # Canlı sipariş kalemi `qtyCanceled` taşıyor: iptal edilen kalem hiç
    # gönderilmedi, geri gelemez. Düşülmezse ekran "iade edilebilir" der ve
    # gönderilmemiş ürün için para iadesi açılır.
    order = {"items": [{"id": 1, "sku": "A", "name": "Ürün", "qtyOrdered": 3,
                        "qtyCanceled": 2, "qtyRefunded": 0, "price": "10.00"}]}
    rows = rma.return_item_rows(order, {})
    assert rows[0]["qtyCanceled"] == 2
    assert rows[0]["maxQty"] == 1
    assert "en çok 1 adet" in rma.selection_error(rows, {1: 2})


def test_talepteki_kalemler_camelcase_anahtarla_da_eslenir() -> None:
    order = {"orderItems": [{"orderItemId": 5, "sku": "B", "name": "Defter",
                             "qtyOrdered": 4, "unitPrice": "3.00"}]}
    rows = rma.return_item_rows(order, {"returnItems": [{"orderItemId": 5, "quantity": 2}]})
    assert rows[0]["itemId"] == 5
    assert rows[0]["unitPrice"] == 300
    assert rows[0]["qty"] == 2
