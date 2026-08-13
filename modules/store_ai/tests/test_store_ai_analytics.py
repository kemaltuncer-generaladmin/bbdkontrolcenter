"""Fark tablosu, bütçe ve kullanım hesabı — saf mantık."""

from __future__ import annotations

from store_ai_backend import analytics


def _payload(**fields: str) -> dict[str, object]:
    return {"suggestions": [{"targetId": 5, "sku": "K-1", "name": "Kalem", "fields": fields,
                             "confidence": 0.82}]}


# ============================================================= fark tablosu

def test_oneri_fark_tablosuna_cevrilir_onceki_deger_korunur() -> None:
    rows = analytics.suggestion_rows(
        "product_description", _payload(description="Yeni metin"),
        before={5: {"description": "Eski metin"}})
    assert len(rows) == 1
    assert rows[0] == {
        "id": "5:description", "targetId": 5, "sku": "K-1", "name": "Kalem",
        "field": "description", "fieldLabel": "Açıklama", "before": "Eski metin",
        "beforeKnown": True, "after": "Yeni metin", "changed": True, "confidence": 82,
        "note": "", "skipped": False, "skipReason": "", "state": analytics.PENDING,
    }


def test_onceki_deger_bilinmiyorsa_bos_denmez_bilinmiyor_denir() -> None:
    # Uydurma eski değer, farkı olduğundan büyük gösterir.
    rows = analytics.suggestion_rows("product_description", _payload(description="Metin"),
                                     before={5: {}})
    assert rows[0]["beforeKnown"] is False
    assert rows[0]["before"] == ""


def test_aracin_yazmadigi_alan_gizlenmez_atlandi_olarak_gosterilir() -> None:
    rows = analytics.suggestion_rows("product_description",
                                     _payload(description="Metin", price="9,99"))
    atlanan = [row for row in rows if row["field"] == "price"]
    assert atlanan and atlanan[0]["skipped"] is True
    assert "bu alana yazmıyor" in atlanan[0]["skipReason"]


def test_bos_metin_oneri_sayilmaz() -> None:
    rows = analytics.suggestion_rows("product_description", _payload(description=""))
    assert rows[0]["skipped"] is True
    assert "boş metin" in rows[0]["skipReason"]


def test_ayni_metin_geldiyse_degisiklik_yok_sayilir() -> None:
    rows = analytics.suggestion_rows("product_description", _payload(description="Aynı"),
                                     before={5: {"description": "Aynı"}})
    assert rows[0]["changed"] is False
    assert analytics.diff_summary(rows)["unchanged"] == 1


def test_tanimayan_arac_hic_satir_uretmez() -> None:
    assert analytics.suggestion_rows("uydurma_arac", _payload(description="Metin")) == []


def test_guven_yuzdesi_yoksa_sifir_degil_bilinmiyor_doner() -> None:
    payload = {"suggestions": [{"targetId": 5, "fields": {"description": "M"}}]}
    assert analytics.suggestion_rows("product_description", payload)[0]["confidence"] is None


# =============================================================== onay/seçim

def test_bos_secim_hepsi_demek_degildir() -> None:
    rows = analytics.suggestion_rows("product_description", _payload(description="Metin"))
    assert analytics.selected_rows(rows, []) == []


def test_yalniz_secilen_satir_uygulanir() -> None:
    payload = {"suggestions": [
        {"targetId": 5, "fields": {"meta_title": "Başlık"}},
        {"targetId": 6, "fields": {"meta_title": "Diğer"}},
    ]}
    rows = analytics.suggestion_rows("seo_meta", payload)
    chosen = analytics.selected_rows(rows, ["5:meta_title"])
    assert [row["targetId"] for row in chosen] == [5]


def test_uygulanan_deger_tabloda_duran_degerin_aynisidir() -> None:
    # Ekranın kimliği: uygulanan şey, kullanıcının okuduğu şeydir.
    rows = analytics.suggestion_rows("product_description", _payload(description="Yeni"))
    yuk = analytics.apply_selection_payload(analytics.selected_rows(rows, ["5:description"]))
    assert yuk == [{"targetId": 5, "field": "description", "value": "Yeni"}]


def test_uygulanan_satir_ikinci_kez_secilemez() -> None:
    rows = analytics.suggestion_rows("product_description", _payload(description="Yeni"))
    sonra = analytics.mark_applied(rows, ["5:description"])
    assert analytics.selected_rows(sonra, ["5:description"]) == []
    assert analytics.diff_summary(sonra)["applied"] == 1


# =================================================================== bütçe

def test_butce_tanimli_degilse_sinir_yok() -> None:
    verdict = analytics.budget_verdict(spent=999_999, budget=0)
    assert verdict["allowed"] is True
    assert verdict["level"] == "ok"


def test_butce_dolunca_varsayilan_davranis_durdurmaktir() -> None:
    verdict = analytics.budget_verdict(spent=50_000, budget=50_000, behavior="block")
    assert verdict["allowed"] is False
    assert verdict["level"] == "block"
    assert "durduruldu" in verdict["message"]


def test_uyar_secilirse_butce_dolsa_bile_calistirma_surer() -> None:
    verdict = analytics.budget_verdict(spent=60_000, budget=50_000, behavior="warn")
    assert verdict["allowed"] is True
    assert verdict["level"] == "warn"


def test_sayac_okunamaz_ve_tahmine_izin_yoksa_para_harcanmaz() -> None:
    # Ölçemediğimiz parayı harcamayız; "herhâlde azdır" varsayımı faturayı büyütür.
    verdict = analytics.budget_verdict(spent=0, budget=50_000, measured=False,
                                       estimate_allowed=False)
    assert verdict["allowed"] is False
    assert "ölçemeden" in verdict["message"]


def test_yuzde_seksende_uyari_cikar_calistirma_durmaz() -> None:
    verdict = analytics.budget_verdict(spent=45_000, budget=50_000)
    assert verdict["allowed"] is True
    assert verdict["level"] == "warn"
    assert verdict["percent"] == 90


def test_tahmini_sayac_ekranda_tahmini_der() -> None:
    verdict = analytics.budget_verdict(spent=10_000, budget=50_000, measured=False)
    assert verdict["message"].startswith("Tahmini:")


def test_aylik_defter_yalniz_o_ayi_toplar() -> None:
    ledger = [{"created_at": "2026-08-03T10:00:00", "cost": 1500},
              {"created_at": "2026-07-30T10:00:00", "cost": 9900}]
    assert analytics.ledger_spend(ledger, month="2026-08") == 1500


# ============================================================ kullanım özeti

def test_magaza_sayaci_yoksa_yerel_defterden_hesaplanir_ve_tahmini_denir() -> None:
    ledger = [{"created_at": "2026-08-03T10:00:00", "cost": 1500, "tokens": 900,
               "tool": "seo_meta"}]
    view = analytics.usage_view(None, ledger, month="2026-08")
    assert view["measured"] is False
    assert view["spent"] == 1500
    assert view["byTool"][0]["label"] == "SEO meta üret"


def test_magaza_ondalik_maliyeti_kurusa_cevrilir() -> None:
    remote = {"daily": [{"date": "2026-08-03", "cost": "12.34"}], "cost": "12.34", "tokens": 90}
    view = analytics.usage_view(remote, [], month="2026-08")
    assert view["measured"] is True
    assert view["spent"] == 1234
    assert view["days"][0]["value"] == 1234


# ============================================================= geçmiş birleşme

def test_magazada_olup_yerelde_olmayan_calisma_gizlenmez() -> None:
    local = [{"runId": 1, "tool": "seo_meta", "createdAt": "2026-08-03T10:00:00"}]
    remote = [{"id": 2, "tool": "seo_meta", "created_at": "2026-08-04T10:00:00",
               "cost": "1.00"}]
    merged = analytics.merge_runs(local, remote)
    assert [row["runId"] for row in merged] == [2, 1]
    assert merged[0]["local"] is False
    assert merged[1]["local"] is True


def test_ayni_calisma_iki_kez_listelenmez() -> None:
    local = [{"runId": 7, "tool": "seo_meta", "createdAt": "2026-08-03T10:00:00"}]
    remote = [{"id": 7, "tool": "seo_meta", "created_at": "2026-08-03T10:00:00"}]
    assert len(analytics.merge_runs(local, remote)) == 1


def test_yerel_ve_magaza_damgalari_ayni_dilde_siralanir() -> None:
    # TUZAK: yerel defter ISO yazıyor ("…T18:27:17"), mağaza saat dilimsiz
    # boşluklu ("… 18:27:17"). Ham metin sıralamasında onuncu karakterde `T`
    # (0x54) ile boşluk (0x20) karşılaşır ve `T` büyüktür; ayırıcıdan SONRAKİ
    # saat hiç okunmaz. Aynı gün içindeki iki çalışmada bu, saat farkını
    # tamamen görmezden gelir: aşağıda mağaza kaydı dokuz saat daha yeni ama
    # eski sürüm yerel kaydı üste alıyordu.
    local = [{"runId": 1, "tool": "seo_meta", "createdAt": "2026-08-13T09:00:00"}]
    remote = [{"id": 2, "tool": "seo_meta", "created_at": "2026-08-13 18:00:00"}]
    assert [row["runId"] for row in analytics.merge_runs(local, remote)] == [2, 1]


def test_ayirici_farki_ayni_ani_esit_sayar() -> None:
    assert analytics.sort_stamp("2026-08-13T18:27:17+03:00") == "2026-08-13 18:27:17"
    assert analytics.sort_stamp("2026-08-13 18:27:17") == "2026-08-13 18:27:17"
