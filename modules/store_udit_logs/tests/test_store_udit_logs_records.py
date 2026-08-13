"""Denetim kaydının saf dönüşümleri. Ağa çıkmaz, DB kullanmaz.

Test adları TUZAĞI söyler: her biri `records.py` başlığındaki üç tuzaktan
birine ya da imleç/fark mantığına karşılık gelir.
"""

from __future__ import annotations

from datetime import datetime

from store_udit_logs_backend import records

# ================================================ TUZAK 1 — saat ekseni

def test_gecidin_utc_damgasi_magazanin_yerel_damgasiyla_ayni_eksene_gelir() -> None:
    # Aynı ANI gösteren iki damga: biri geçidin UTC yazımı, biri mağazanın
    # yerel yazımı. Ham metin olarak sıralanırlarsa üç saat kayarlar.
    instant = datetime.fromisoformat("2026-08-13T07:22:31+00:00")
    local = instant.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    gateway = records.normalize_stamp("2026-08-13T07:22:31+00:00", assume_utc=True)
    store = records.normalize_stamp(local)

    assert gateway == store


def test_saat_dilimsiz_magaza_damgasi_yerel_kabul_edilir() -> None:
    assert records.normalize_stamp("2026-08-13 10:22:31") == "2026-08-13T10:22:31"


def test_okunamayan_damga_bos_doner_uydurulmaz() -> None:
    assert records.normalize_stamp("dün akşam") == ""
    assert records.normalize_stamp(None) == ""


# ================================================= zorunlu tarih aralığı

def test_bitis_gunu_gun_sonuna_genisletilir() -> None:
    # Genişletilmezse o günün 00:00'dan sonraki bütün kayıtları düşer ve
    # kullanıcı "bugün hiçbir şey olmamış" sanır.
    start, end, problem = records.range_bounds("2026-08-01", "2026-08-13")
    assert problem == ""
    assert start == "2026-08-01T00:00:00"
    assert end == "2026-08-13T23:59:59"


def test_saatli_aralik_oldugu_gibi_korunur() -> None:
    start, end, problem = records.range_bounds("2026-08-13T14:00", "2026-08-13T18:30")
    assert (start, end, problem) == ("2026-08-13T14:00:00", "2026-08-13T18:30:00", "")


def test_aralik_zorunludur() -> None:
    assert records.range_bounds("", "2026-08-13")[2]
    assert records.range_bounds("2026-08-13", "")[2]


def test_ters_aralik_reddedilir() -> None:
    assert "Başlangıç" in records.range_bounds("2026-08-13", "2026-08-01")[2]


def test_aralik_gun_sayisi_ucuna_dahil_sayar() -> None:
    start, end, _ = records.range_bounds("2026-08-01", "2026-08-01")
    assert records.range_days(start, end) == 1


# ======================================================== varlık çıkarımı

def test_php_sinif_adi_varliga_cevrilir() -> None:
    assert records.entity_from_type("Webkul\\Sales\\Models\\Order") == "order"
    assert records.entity_from_type("Webkul\\Catalog\\Models\\ProductProxy") == "product"
    assert records.entity_from_type("Webkul\\CMS\\Models\\CmsPage") == "cms_page"


def test_gecit_satirinda_varlik_yoldan_okunur() -> None:
    assert records.entity_from_path("/api/admin/catalog/products/5") == "product"
    assert records.entity_id_from_path("/api/admin/catalog/products/5") == 5
    assert records.entity_from_path("/api/admin/bbd/return-requests/12") == "return_request"


def test_taninmayan_yolda_varlik_uydurulmaz() -> None:
    # Yanlış varlık adı `[Kayda git]` düğmesini yanlış panele yollar.
    assert records.entity_from_path("/api/admin/bilinmeyen/7") == ""


# =============================================================== fark

def test_fark_tablosu_degiseni_isaretler() -> None:
    diff = records.diff_fields({"price": "10.00", "name": "Kalem"},
                               {"price": "12.00", "name": "Kalem"})
    changed = {row["field"]: row["changed"] for row in diff}
    assert changed == {"price": True, "name": False}


def test_alanin_hic_olmamasi_ile_none_olmasi_ayri_seydir() -> None:
    # Oluşturma kaydında `before` boştur; "yoktu → None" bir DEĞİŞİKLİKTİR.
    diff = records.diff_fields({}, {"note": None})
    assert diff[0]["changed"] is True


def test_sir_tasiyan_alan_maskelenir_ama_varligi_gizlenmez() -> None:
    diff = records.diff_fields({"api_token": "eski"}, {"api_token": "yeni-gizli"})
    row = diff[0]
    assert row["field"] == "api_token"
    assert "gizli" not in row["after"]
    assert row["after"] == "•••"


def test_ozet_degisen_alan_adlarini_yazar() -> None:
    diff = records.diff_fields({"a": 1, "b": 1, "c": 1}, {"a": 2, "b": 1, "c": 3})
    assert records.summary_text(diff) == "a, c"


# ========================================================== yıkıcı işlem

def test_yikici_islem_fiilden_ve_yontemden_anlasilir() -> None:
    assert records.is_destructive("delete_product") is True
    assert records.is_destructive("cancel_order") is True
    assert records.is_destructive("", "DELETE") is True
    assert records.is_destructive("update_product") is False


# ================================================================ satır

def test_uzak_satir_alan_adi_degisse_de_okunur() -> None:
    # Bagisto sürümüne göre `old_values`/`before` gibi adlar değişiyor; tek ada
    # bel bağlamak ekranı sürüm yükseltmesinde sessizce boşaltırdı.
    row = records.remote_row({
        "id": 7, "subject_type": "Webkul\\Sales\\Models\\Order", "subject_id": 12,
        "event": "updated", "before": {"status": "pending"}, "after": {"status": "canceled"},
        "user": {"name": "Ayşe"}, "ip": "10.0.0.4", "created_at": "2026-08-13 10:00:00",
    })
    assert row["entity"] == "order"
    assert row["entityId"] == 12
    assert row["user"] == "Ayşe"
    assert row["summary"] == "status"
    assert row["resultLabel"] == "Başarılı"
    assert row["key"] == "r:7"


def test_json_metni_olarak_gelen_fark_sozluk_sanilmaz() -> None:
    row = records.remote_row({"id": 1, "old_values": '{"price": "1.00"}',
                              "new_values": '{"price": "2.00"}',
                              "created_at": "2026-08-13 10:00:00"})
    assert row["summary"] == "price"


def test_gecit_satiri_gerekce_ve_kuru_provayi_tasir() -> None:
    row = records.local_row({
        "id": 3, "request_id": "abc", "method": "POST", "path": "/api/admin/sales/orders/9/cancel",
        "action": "cancel_order", "reason": "Müşteri vazgeçti, stok geri alınacak",
        "actor": "Mehmet", "dry_run": 1, "body": '{"reason": "iptal"}', "result": "dry_run",
        "created_at": "2026-08-13T07:00:00+00:00",
    })
    assert row["source"] == "gateway"
    assert row["entity"] == "order"
    assert row["entityId"] == 9
    assert row["dryRun"] is True
    assert row["destructive"] is True
    assert row["resultLabel"] == "Kuru prova"


# ====================================== TUZAK 2 — çift kayıt / gerekçe

def test_basarili_gecit_satiri_listede_tekrar_etmez() -> None:
    rows = [
        records.local_row({"id": 1, "request_id": "a", "result": "ok",
                           "created_at": "2026-08-13T07:00:00+00:00"}),
        records.local_row({"id": 2, "request_id": "b", "result": "blocked",
                           "created_at": "2026-08-13T07:01:00+00:00"}),
    ]
    only = records.local_only(rows)
    assert [row["requestId"] for row in only] == ["b"]


def test_gerekce_uzak_satira_istek_kimliginden_gecer() -> None:
    remote = [records.remote_row({"id": 5, "request_id": "a", "action": "updated",
                                  "created_at": "2026-08-13 10:00:00"})]
    local = [records.local_row({"id": 1, "request_id": "a", "result": "ok", "actor": "Ayşe",
                                "reason": "Fiyat listesi güncellendi",
                                "created_at": "2026-08-13T07:00:00+00:00"})]
    records.join_reason(remote, local)
    assert remote[0]["reason"] == "Fiyat listesi güncellendi"
    assert remote[0]["user"] == "Ayşe"


# =============================================================== süzme

def _row(**over: object) -> dict[str, object]:
    base = records.remote_row({"id": 1, "action": "updated", "auditable_type": "Order",
                               "auditable_id": 4, "created_at": "2026-08-13 10:00:00",
                               "ip_address": "10.0.0.4",
                               "user": {"name": "Ayşe"}})
    base.update(over)
    return base


def test_arama_gerekce_metninde_de_arar() -> None:
    row = _row(reason="Müşteri şikâyeti üzerine iptal")
    assert records.matches(row, {"q": "sikayet"}) is True
    assert records.matches(row, {"q": "kargo"}) is False


def test_anahtarlar_yikici_ve_gerekceli_isleme_daraltir() -> None:
    plain = _row()
    assert records.matches(plain, {"destructive": True}) is False
    assert records.matches(_row(destructive=True), {"destructive": True}) is True
    assert records.matches(plain, {"reasoned": True}) is False
    assert records.matches(_row(reason="var"), {"reasoned": True}) is True


def test_aralik_disindaki_satir_elenir() -> None:
    row = _row()
    assert records.matches(row, {"start": "2026-08-13T00:00:00",
                                 "end": "2026-08-13T23:59:59"}) is True
    assert records.matches(row, {"start": "2026-08-14T00:00:00"}) is False


def test_uzak_sorgu_parametreleri_sozlesmeye_gore_adlandirilir() -> None:
    sent = records.remote_filters({"start": "2026-08-01T00:00:00", "end": "2026-08-13T23:59:59",
                                   "entityId": 5, "destructive": True, "q": "iptal"})
    assert sent == {"from": "2026-08-01T00:00:00", "to": "2026-08-13T23:59:59",
                    "q": "iptal", "entity_id": 5, "destructive": 1}


# ============================================================== imleç

def test_imlec_gidip_geri_gelir() -> None:
    token = records.encode_cursor(150, 4, "2026-08-13T10:00:00|store|000000000005|r:5")
    assert records.decode_cursor(token) == (
        150, 4, "2026-08-13T10:00:00|store|000000000005|r:5")


def test_bos_imlec_listenin_basidir() -> None:
    assert records.decode_cursor("") == (0, 0, "")


def test_bozuk_imlec_sessizce_basa_donmez() -> None:
    # `None` = "imleci okuyamadım"; ekran bunu söyler. Sessizce başa dönmek,
    # kullanıcıya farkında olmadan aynı sayfayı tekrar gösterirdi.
    assert records.decode_cursor("bu-imleç-değil!!") is None


def _stamped(key: str, at: str, source: str = "store") -> dict[str, object]:
    return {"key": key, "sortKey": f"{at}|{source}|{0:012d}|{key}", "source": source,
            "at": at, "destructive": False, "reason": ""}


def test_iki_kaynak_zamana_gore_ic_ice_dizilir() -> None:
    remote = [_stamped("r:2", "2026-08-13T10:00:00"), _stamped("r:1", "2026-08-13T08:00:00")]
    local = [_stamped("g:b", "2026-08-13T09:00:00", "gateway"),
             _stamped("g:a", "2026-08-13T07:00:00", "gateway")]
    page = records.merge_page(remote, local, size=10)
    assert [row["key"] for row in page["rows"]] == ["r:2", "g:b", "r:1", "g:a"]
    assert page["more"] is False


def test_sayfa_dolunca_kullanilan_ofsetler_geri_verilir() -> None:
    remote = [_stamped("r:3", "2026-08-13T10:00:00"), _stamped("r:2", "2026-08-13T09:30:00")]
    local = [_stamped("g:b", "2026-08-13T09:00:00", "gateway")]
    page = records.merge_page(remote, local, size=2)
    assert [row["key"] for row in page["rows"]] == ["r:3", "r:2"]
    assert (page["usedRemote"], page["usedLocal"]) == (2, 0)
    assert page["more"] is True


def test_araya_giren_yeni_kayit_onceki_sayfayi_tekrar_ettirmez() -> None:
    # İkinci sayfa çekilirken listeye yeni bir kayıt düşerse ofset tek başına
    # önceki sayfanın son satırını geri getirirdi; `after` sınırı onu keser.
    remote = [_stamped("r:9", "2026-08-13T11:00:00"), _stamped("r:2", "2026-08-13T09:00:00")]
    page = records.merge_page(remote, [], size=10,
                              after=f"2026-08-13T10:00:00|store|{0:012d}|r:5")
    assert [row["key"] for row in page["rows"]] == ["r:2"]
    # Elenen satır SAYILIR: ofset kaynaktaki gerçek konumu göstermeli.
    assert page["usedRemote"] == 2


def test_elenen_satir_sayfayi_delik_birakmaz_ama_ofseti_ilerletir() -> None:
    remote = [_stamped("r:3", "2026-08-13T10:00:00"), _stamped("r:2", "2026-08-13T09:00:00")]
    page = records.merge_page(remote, [], size=10, keep=lambda row: row["key"] != "r:3")
    assert [row["key"] for row in page["rows"]] == ["r:2"]
    assert page["usedRemote"] == 2


# ============================ TUZAK 3'ÜN CANLI HÂLİ — camelCase alan adları

def test_magazanin_camelcase_alanlari_da_okunur() -> None:
    # Bagisto'nun yönetici zarfı sütun adlarını camelCase'e çeviriyor; canlı
    # mağazada doğrulandı (`incrementId`, `grandTotal`, `createdAt`…). Yalnız
    # snake_case aramak ekranı SESSİZCE boşaltırdı: satır gelir, fark tablosu
    # boş çıkar, "Varlık" ve "IP" sütunları "—" dolar.
    row = records.remote_row({
        "id": 41,
        "auditableType": "Webkul\\Sales\\Models\\Order",
        "auditableId": 19,
        "action": "updated",
        "oldValues": {"status": "pending"},
        "newValues": {"status": "processing"},
        "userName": "Kemal Tuncer",
        "ipAddress": "88.230.1.9",
        "requestId": "abc",
        "statusCode": 200,
        "createdAt": "2026-08-13T18:27:17+03:00",
    })
    assert row["entity"] == "order"
    assert row["entityId"] == 19
    assert row["user"] == "Kemal Tuncer"
    assert row["ip"] == "88.230.1.9"
    assert row["requestId"] == "abc"
    assert row["summary"] == "status"
    assert row["diff"][0]["changed"] is True


def test_snake_case_alanlar_da_calismaya_devam_eder() -> None:
    row = records.remote_row({
        "id": 7, "auditable_type": "Webkul\\Catalog\\Models\\Product", "auditable_id": 5,
        "old_values": {"price": "1"}, "new_values": {"price": "2"},
        "ip_address": "10.0.0.4", "created_at": "2026-08-13 10:00:00",
    })
    assert (row["entity"], row["entityId"], row["ip"]) == ("product", 5, "10.0.0.4")


# ================================ geçidin GERÇEK yolları (client.py sözleşmesi)

def test_gecidin_gercek_yollari_taninir() -> None:
    # Canlı mağazada doğrulandı: `/api/admin/sales/orders`, `/promotions/*`,
    # `/settings/taxes/*` ve `/reviews` YOKTUR (404). Yol deseni uydurulursa
    # "Varlık" sütunu "—" dolar ve `[Kayda git]` ömür boyu pasif kalır.
    beklenen = {
        "/api/admin/orders/12/cancel": "order",
        "/api/admin/invoices/8/send-duplicate": "invoice",
        "/api/admin/refunds": "refund",
        "/api/admin/shipments/4": "shipment",
        "/api/admin/transactions/3": "transaction",
        "/api/admin/marketing/cart-rules/3/coupons": "cart_rule",
        "/api/admin/marketing/catalog-rules/2": "catalog_rule",
        "/api/admin/settings/tax-rates/5": "tax_rate",
        "/api/admin/settings/tax-categories": "tax_category",
        "/api/admin/customers/reviews/3": "review",
        "/api/admin/customers/groups": "customer_group",
        "/api/admin/customers/9/notes": "customer",
        "/api/admin/bbd/payments/attempts/9/refund": "transaction",
        "/api/admin/bbd/trial-club/exams/4/results/publish": "trial_exam",
    }
    for path, entity in beklenen.items():
        assert records.entity_from_path(path) == entity, path


def test_fatura_olusturma_yolu_siparise_baglanir() -> None:
    # `/orders/12/invoices` yolundaki 12 SİPARİŞİN numarasıdır. Fatura desenini
    # önce koymak satırı "Fatura #12" yapardı ve `[Kayda git]` yanlış kaydı açardı.
    assert records.entity_from_path("/api/admin/orders/12/invoices") == "order"
    assert records.entity_id_from_path("/api/admin/orders/12/invoices") == 12


def test_kayit_numarasi_yoldaki_ILK_sayidir() -> None:
    # Son sayı görselin numarasıdır, ürünün değil.
    assert records.entity_id_from_path("/api/admin/catalog/products/1428/images/7") == 1428


# ====================================================== damgasız satır

def test_damgasiz_satir_listenin_basina_oturmaz() -> None:
    # Ham "" ile başlayan sıralama anahtarı, ayraç '|' rakamlardan büyük olduğu
    # için AZALAN sıralamada en üste çıkardı: tarihsiz bir satır her sayfanın
    # ilk satırı olurdu.
    stampless = records.remote_row({"id": 1, "action": "updated"})
    dated = records.remote_row({"id": 2, "action": "updated",
                                "createdAt": "2026-08-13T10:00:00"})
    assert stampless["at"] == ""
    assert dated["sortKey"] > stampless["sortKey"]


# ============================================ süzgecin uzağa giden parçası

def test_uzaga_gitmeyen_suzgec_sunucunun_kusuru_sayilmaz() -> None:
    # `source` ve `reasoned` mağazaya HİÇ gönderilmez; onlar yüzünden elenen
    # satırı "sunucu süzgeci uygulamadı" diye saymak doğru çalışan bir uca
    # haksız uyarı astırırdı.
    scope = records.remote_scope({"start": "2026-08-13T00:00:00", "end": "2026-08-13T23:59:59",
                                  "source": "gateway", "reasoned": True, "entity": "order"})
    assert "source" not in scope
    assert "reasoned" not in scope
    assert scope["entity"] == "order"


def test_gecidin_her_yazma_yolu_bir_varliga_baglanir() -> None:
    # Eşlemesi olmayan yazma yolu tabloda "—" olarak görünür ve süzgeçten de
    # düşer: "bugün ne yapıldı" sorusu o satırları hiç görmez.
    beklenen = {
        "/api/admin/marketing/campaigns/1/send": "campaign",
        "/api/admin/marketing/sitemaps/1/generate": "sitemap",
        "/api/admin/marketing/url-rewrites/1": "url_rewrite",
        "/api/admin/marketing/search-synonyms/1": "search_synonym",
        "/api/admin/settings/exchange-rates/update-rates": "exchange_rate",
        "/api/admin/settings/roles/1": "role",
        "/api/admin/catalog/families/2": "attribute_family",
        "/api/admin/bbd/ai/tools/seo/run": "ai_run",
        "/api/admin/bbd/bld/jobs/3/retry": "bld_job",
        "/api/admin/bbd/bld/orders/12/reprint": "order",      # `/bbd/bld`ten önce
        "/api/admin/bbd/carriers/yurtici/test": "carrier",
        "/api/admin/bbd/catalog/reindex": "catalog",
        "/api/admin/bbd/shipping/rates": "shipping_rate",
        "/api/admin/bbd/mobile/settings": "mobile_setting",
    }
    for path, entity in beklenen.items():
        assert records.entity_from_path(path) == entity, path
        # Etiketi olmayan varlık ekranda ham İngilizce ada düşerdi.
        assert entity in records.ENTITY_LABELS, entity
