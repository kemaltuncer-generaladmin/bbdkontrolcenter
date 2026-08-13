"""Dahili AI servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from store_ai_backend import analytics, rules
from store_ai_backend import service as service_module
from store_ai_backend.service import AiService
from store_ai_fakes import FakeApi, FakeLog, FakeStore, live_product, product

TOOLS = {"items": [{"key": "product_description"}, {"key": "seo_meta"},
                   {"key": "review_sentiment"}]}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[AiService, FakeApi, FakeStore]:
    api = api or FakeApi({5: product(5)})
    api.tools_payload = dict(TOOLS)
    store = store or FakeStore()
    service = AiService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "monthly_budget": 0,
                "budget_behavior": "block", "budget_estimate_when_offline": True,
                "scan_ttl_seconds": 900, "scan_row_cap": 3000,
                "min_description_chars": 120, "meta_title_max": 60,
                "meta_description_min": 70, "meta_description_max": 160,
                "price_outlier_factor": 5, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


async def _run(service: AiService, api: FakeApi, *, tool: str = "product_description",
               dry_run: bool = False) -> dict[str, Any]:
    api.run_payload = {"runId": 42, "tokens": 300, "cost": "1.50", "suggestions": [
        {"targetId": 5, "sku": "SKU-5", "name": "Ürün 5",
         "fields": {"description": "Model tarafından önerilen yeni açıklama."}}]}
    return await service.run_tool(tool, target_ids=[5], params={},
                                  reason="Açıklamalar zenginleştiriliyor", actor="Ali",
                                  dry_run=dry_run)


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_katalog_denetimi_ekrani_dusurmez() -> None:
    service, api, _ = _service()
    api.fail.add("products")
    result = await service.catalog()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["findings"] == []
    assert "patladı" in result["error"]


async def test_ai_ucu_yayinda_degilken_katalog_sagligi_yine_calisir() -> None:
    # Ekranın yarısı KURAL TABANLIDIR: BBD/AI uçları hiç yayınlanmasa bile
    # görselsiz/kategorisiz ürün bulguları bugün çıkar.
    service, api, _ = _service()
    api.missing.add("bbd_ai_tools")
    api.list_payload = {"items": [product(1, images=[])], "meta": {}, "truncated": False}

    tools = await service.tools()
    assert tools["endpointReady"] is False
    assert all(card["runnable"] is False for card in tools["tools"])
    assert "uç hazır olunca" in tools["tools"][0]["blockReason"]

    catalog = await service.catalog()
    assert catalog["connected"] is True
    assert catalog["summary"]["total"] >= 1


async def test_kategori_agaci_okunamazsa_bos_kategori_denetimi_kapanir_gerisi_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("category_tree")
    api.list_payload = {"items": [product(1, images=[])], "meta": {}, "truncated": False}
    result = await service.catalog()
    assert result["connected"] is True
    assert any("Kategori ağacı okunamadı" in item for item in result["warnings"])
    assert result["summary"]["total"] == 1


# =========================================================== katalog taraması

async def test_tarama_tam_katalogu_sirayla_ceker_ve_kanal_dil_gonderir() -> None:
    # KANAL SÜZGECİ KOD İSTER, KİMLİK DEĞİL. Canlıda ölçüldü (2026-08-13):
    # `channel=default` → 1.421 kayıt, `channel=1` → 0 kayıt. Sipariş ucunun
    # tersidir; oraya kanal kodu göndermek listeyi boşaltırdı, buraya kanal
    # kimliği göndermek boşaltır.
    service, api, _ = _service()
    await service.catalog()
    call = api.used("products")[0]
    assert call["all_pages"] is True
    assert api.calls[0][1][0] == {"channel": "default", "locale": "tr"}


async def test_canli_bicimli_katalog_yanlis_bulgu_uretmez() -> None:
    # EKRANIN YARISININ SINAVI. Mağaza alan adlarını camelCase veriyor;
    # snake_case okuyan sürüm bu üç sağlam ürünün her birine "Görsel yok" +
    # "Kategorisiz" + "SEO eksik" diyordu — 1.421 üründe ~4.300 uydurma bulgu,
    # sıfır gerçek bulgu. Hiçbiri istisna atmıyordu.
    service, api, _ = _service()
    api.list_payload = {"items": [live_product(1), live_product(2), live_product(3)],
                        "meta": {}, "truncated": False}
    result = await service.catalog()
    assert result["connected"] is True
    assert result["findings"] == []
    assert result["summary"]["scanned"] == 3
    assert result["summary"]["clean"] == 3


async def test_canli_bicimde_gercek_eksiklik_yine_bulunur() -> None:
    # Düzeltme kuralları köreltmemeli: gerçekten görselsiz ve meta başlıksız
    # ürün hâlâ yakalanmalı.
    service, api, _ = _service()
    api.list_payload = {"items": [live_product(1, imagesCount=0, baseImageUrl=None,
                                               metaTitle="")],
                        "meta": {}, "truncated": False}
    kinds = {item["kind"] for item in (await service.catalog())["findings"]}
    assert kinds == {"no_image", "seo_broken"}


async def test_kategori_bilgisi_eksikse_bos_kategori_denetimi_kendiliginden_kapanir() -> None:
    # Liste ucu ürünün yalnız BİRİNCİL kategorisini veriyor (canlıda 1418
    # numaralı ürün dört kategoride, listede bir tanesi görünüyor). Eksik
    # veriyle sayınca ürünü olan kategoriler "boş" çıkardı; barkod kuralında
    # olduğu gibi susmak yanlış söylemekten iyidir.
    service, api, _ = _service()
    api.list_payload = {"items": [live_product(1)], "meta": {}, "truncated": False}
    api.tree_payload = {"items": [{"id": 2, "name": "TYT"}, {"id": 4, "name": "Matematik"}]}
    result = await service.catalog()
    assert api.used("category_tree") == []          # ağaç istenmedi bile
    assert not [item for item in result["findings"] if item["kind"] == "empty_category"]
    assert any("birincil kategoriyi" in item for item in result["warnings"])


async def test_kapali_kural_adlari_ekranda_turkce_gorunur() -> None:
    # Kural anahtarı (`empty_category`) İngilizce ve ASCII'dir; arayüz metni
    # Türkçe. Ham anahtarı ekrana basmak kullanıcıya hiçbir şey anlatmıyordu.
    service, api, _ = _service()
    api.list_payload = {"items": [live_product(1)], "meta": {}, "truncated": False}
    disabled = (await service.catalog())["summary"]["disabledRules"]
    assert "Boş kategori" in disabled
    assert "empty_category" not in disabled


async def test_tarama_bellekte_taze_tutulur_her_acilista_yeniden_taranmaz() -> None:
    service, api, _ = _service()
    await service.catalog()
    await service.catalog()
    assert len(api.used("products")) == 1
    await service.catalog(refresh=True)
    assert len(api.used("products")) == 2


async def test_esik_degisince_bayat_tarama_atilir() -> None:
    # Eşik değişip tarama korunsaydı ekran YANLIŞ bulgu gösterirdi.
    service, api, _ = _service()
    await service.catalog()
    await service.save_settings(values={"min_description_chars": 400},
                                reason="Açıklama eşiği yükseltildi", actor="Ali")
    await service.catalog()
    assert len(api.used("products")) == 2


async def test_katalog_kirpilirsa_ekran_bunu_soyler() -> None:
    service, api, _ = _service(scan_row_cap=1)
    api.list_payload = {"items": [product(1), product(2)], "meta": {}, "truncated": True}
    result = await service.catalog()
    assert result["truncated"] is True
    assert any("kırpıldı" in item for item in result["warnings"])


# ============================================================ bulgu susturma

async def test_susturulan_bulgu_listede_gorunmez_gerekce_kaydedilir() -> None:
    service, api, store = _service()
    api.list_payload = {"items": [product(1, images=[])], "meta": {}, "truncated": False}
    before = await service.catalog()
    key = before["findings"][0]["id"]

    await service.mute(finding_key=key, kind="no_image", target_id=1,
                       reason="Görsel tedarikçiden bekleniyor", actor="Ali")
    after = await service.catalog()
    assert [item["id"] for item in after["findings"]] == []
    assert after["summary"]["muted"] == 1
    assert store.ignored[0]["reason"] == "Görsel tedarikçiden bekleniyor"


async def test_susturma_satir_silinerek_degil_eklenerek_geri_alinir() -> None:
    service, api, store = _service()
    api.list_payload = {"items": [product(1, images=[])], "meta": {}, "truncated": False}
    key = (await service.catalog())["findings"][0]["id"]
    await service.mute(finding_key=key, kind="no_image", target_id=1,
                       reason="Görsel tedarikçiden bekleniyor", actor="Ali")
    await service.unmute(finding_key=key, reason="Görsel yüklendi, tekrar bakılsın",
                         actor="Ali")
    assert len(store.ignored) == 2                      # hiçbir satır silinmedi
    assert (await service.catalog())["findings"]


async def test_kisa_gerekceyle_bulgu_susturulamaz() -> None:
    service, _, store = _service()
    result = await service.mute(finding_key="no_image:1", kind="no_image", target_id=1,
                                reason="ok", actor="Ali")
    assert result["ok"] is False
    assert store.ignored == []


# ============================================================== çalıştırma

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="ok", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_ai_run") == []


async def test_tanimayan_arac_calistirilmaz() -> None:
    service, api, _ = _service()
    result = await service.run_tool("uydurma", target_ids=[5], params={},
                                    reason="Deneme yapılıyor şimdi", actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_ai_run") == []


async def test_calistirma_hicbir_seyi_yazmaz_yalniz_oneri_uretir() -> None:
    service, api, _ = _service()
    result = await _run(service, api)
    assert result["ok"] is True
    assert result["rows"][0]["after"].startswith("Model tarafından")
    assert api.used("bbd_ai_apply") == []


async def test_onceki_deger_urunden_okunur_ve_fark_tablosuna_girer() -> None:
    # "Önce" sütunu alanın HAM değerini taşır (etiketler dahil): uygulama bu
    # metnin YERİNE yazacak, o yüzden kullanıcı gerçekte neyin gideceğini
    # görmeli. Etiket temizliği yalnız katalog denetiminde uzunluk ölçerken
    # yapılır.
    service, api, _ = _service()
    result = await _run(service, api)
    assert result["rows"][0]["beforeKnown"] is True
    assert result["rows"][0]["before"].startswith("<p>Bu ürün hakkında")


async def test_once_sutunu_canli_camelcase_kayittan_da_okunur() -> None:
    # TUZAK 0'ın en sinsi yüzü: `has_key(raw, "meta_title")` canlıdaki
    # `metaTitle` alanını görmezse fark tablosunun "Önce" sütunu her satırda
    # "—" der. Ekran patlamaz, kullanıcı neyin üstüne yazdığını göremez.
    api = FakeApi({5: live_product(5, metaTitle="Eski başlık",
                                   metaDescription="Eski açıklama")})
    service, api, _ = _service(api)
    api.run_payload = {"runId": 42, "tokens": 100, "cost": "0.50", "suggestions": [
        {"targetId": 5, "fields": {"meta_title": "Yeni başlık",
                                   "meta_description": "Yeni açıklama"}}]}
    result = await service.run_tool("seo_meta", target_ids=[5], params={},
                                    reason="Meta alanları yenileniyor", actor="Ali",
                                    dry_run=False)
    rows = {row["field"]: row for row in result["rows"]}
    assert rows["meta_title"]["beforeKnown"] is True
    assert rows["meta_title"]["before"] == "Eski başlık"
    assert rows["meta_description"]["before"] == "Eski açıklama"


async def test_calisma_numarasi_zarf_icinde_gelse_de_okunur() -> None:
    # ÖNCEKİ HATA: `data` sözlükse yalnız `data.id` bakılıyor, o anahtar yoksa
    # `runId`'ye hiç düşülmüyordu. Numara 0 kalınca uygulama düğmesi sessizce
    # kapalı kalıyordu — öneri görünür, uygulanamazdı.
    service, api, _ = _service()
    api.run_payload = {"data": {"runId": 42}, "tokens": 10, "cost": "0.10",
                       "suggestions": [{"targetId": 5, "fields": {"description": "Metin"}}]}
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="Açıklama üretimi yapılıyor", actor="Ali",
                                    dry_run=False)
    assert result["runId"] == 42
    assert result["applicable"] is True


async def test_kuru_prova_jeton_harcamaz_ve_uygulama_acilmaz() -> None:
    service, api, _ = _service()
    result = await _run(service, api, dry_run=True)
    assert result["dryRun"] is True
    assert result["applicable"] is False
    assert "jeton harcanmadı" in result["note"]


async def test_hedef_siniri_asilirsa_hic_istek_gitmez() -> None:
    service, api, _ = _service()
    result = await service.run_tool("product_description", target_ids=list(range(1, 40)),
                                    params={}, reason="Toplu açıklama üretiliyor", actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_ai_run") == []


async def test_ai_ucu_yoksa_anlasilir_mesaj_doner_sessizce_patlamaz() -> None:
    service, api, _ = _service()
    api.missing.add("bbd_ai_run")
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="Açıklama üretimi deneniyor", actor="Ali",
                                    dry_run=False)
    assert result["ok"] is False
    assert "uç hazır olunca" in result["error"]
    assert result["endpointReady"] is False


async def test_kapatilan_arac_calistirilmaz() -> None:
    service, api, _ = _service()
    await service.save_settings(values={"disabled_tools": ["product_description"]},
                                reason="Bu araç geçici kapatıldı", actor="Ali")
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="Açıklama üretimi deneniyor", actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_ai_run") == []


# ================================================================== bütçe

async def test_butce_dolunca_calistirma_reddedilir_ve_istek_gitmez() -> None:
    service, api, store = _service(monthly_budget=100)
    await _run(service, api)                     # 150 kuruş harcadı
    api.calls.clear()
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="Bir kez daha denenecek", actor="Ali",
                                    dry_run=False)
    assert result["ok"] is False
    assert "bütçe doldu" in result["error"]
    assert api.used("bbd_ai_run") == []
    assert any(row["result"] == "reddedildi" for row in store.audit)


async def test_uyar_secilirse_butce_dolsa_bile_calistirma_surer() -> None:
    service, api, _ = _service(monthly_budget=100, budget_behavior="warn")
    await _run(service, api)
    api.calls.clear()
    result = await _run(service, api)
    assert result["ok"] is True
    assert result["budget"]["level"] == "warn"


async def test_magaza_sayaci_okunamazsa_yerel_defter_kullanilir() -> None:
    service, api, _ = _service(monthly_budget=10_000)
    api.fail.add("bbd_ai_usage")
    await _run(service, api)
    state = await service.budget_state()
    assert state["measured"] is False
    assert state["spent"] == 150
    assert state["allowed"] is True


async def test_uc_yanit_verse_bile_sayac_tasimiyorsa_olculdu_denmez() -> None:
    # Yanıt gelmesi, sayacın geldiği anlamına gelmiyor. O yanıtı "ölçüldü"
    # saymak, yerel defterden gelen rakamı kesin gibi gösterirdi.
    service, api, _ = _service(monthly_budget=10_000)
    api.usage_payload = {"ok": True}
    await _run(service, api)
    state = await service.budget_state()
    assert state["measured"] is False
    assert state["message"].startswith("Tahmini:")


async def test_magaza_sayaci_yerel_defterden_buyukse_o_kullanilir() -> None:
    # Yerel defter Kontrol Merkezi dışındaki çalıştırmaları görmez; küçüğünü
    # seçmek bütçeyi delerdi.
    service, api, _ = _service(monthly_budget=10_000)
    api.usage_payload = {"daily": [{"date": "2026-08-01", "cost": "90.00"}], "cost": "90.00"}
    await _run(service, api)
    state = await service.budget_state()
    assert state["measured"] is True
    assert state["spent"] == 9_000


async def test_sayac_okunamaz_ve_tahmine_izin_yoksa_para_harcanmaz() -> None:
    service, api, _ = _service(monthly_budget=10_000, budget_estimate_when_offline=False)
    api.fail.add("bbd_ai_usage")
    result = await service.run_tool("product_description", target_ids=[5], params={},
                                    reason="Açıklama üretimi deneniyor", actor="Ali",
                                    dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_ai_run") == []


# ============================================================== uygulama

async def test_onizlemesiz_uygulama_yapilmaz() -> None:
    service, _, _ = _service()
    result = await service.apply(token="olmayan", selections=["5:description"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "Fark tablosu görülmeden" in result["error"]


async def test_bos_secim_hepsi_demek_degildir() -> None:
    service, api, _ = _service()
    run = await _run(service, api)
    result = await service.apply(token=run["token"], selections=[],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_ai_apply") == []


async def test_uygulanan_deger_onizlenen_degerin_aynisidir() -> None:
    # Ekranın kimliği: uygulama fark tablosunu YENİDEN HESAPLAMAZ.
    service, api, store = _service()
    run = await _run(service, api)
    saklanan = json.loads(store.runs[run["token"]]["rows"])[0]["after"]
    result = await service.apply(token=run["token"], selections=["5:description"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is True
    gonderilen = api.used("bbd_ai_apply")[0]["selections"]
    assert gonderilen == [{"targetId": 5, "field": "description", "value": saklanan}]


async def test_ayni_oneri_iki_kez_uygulanmaz() -> None:
    service, api, _ = _service()
    run = await _run(service, api)
    await service.apply(token=run["token"], selections=["5:description"],
                        reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    ikinci = await service.apply(token=run["token"], selections=["5:description"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert ikinci["ok"] is False
    assert len(api.used("bbd_ai_apply")) == 1


async def test_yazmayan_arac_uygulanamaz() -> None:
    service, api, _ = _service()
    api.run_payload = {"runId": 7, "tokens": 10, "cost": "0.10",
                       "summary": "Yorumların %80'i olumlu."}
    run = await service.run_tool("review_sentiment", target_ids=[], params={"days": 30},
                                 reason="Yorum duygu özeti alınıyor", actor="Ali",
                                 dry_run=False)
    assert run["readOnly"] is True
    result = await service.apply(token=run["token"], selections=["1:x"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "yazmaz" in result["error"]


async def test_magaza_calisma_numarasi_vermediyse_uygulama_kapali_kalir() -> None:
    service, api, _ = _service()
    api.run_payload = {"tokens": 10, "cost": "0.10", "suggestions": [
        {"targetId": 5, "fields": {"description": "Metin"}}]}
    run = await service.run_tool("product_description", target_ids=[5], params={},
                                 reason="Açıklama üretimi yapılıyor", actor="Ali",
                                 dry_run=False)
    assert run["applicable"] is False
    result = await service.apply(token=run["token"], selections=["5:description"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_ai_apply") == []


async def test_uygulama_ucu_yoksa_anlasilir_mesaj_doner() -> None:
    service, api, _ = _service()
    run = await _run(service, api)
    api.missing.add("bbd_ai_apply")
    result = await service.apply(token=run["token"], selections=["5:description"],
                                 reason="Öneriler uygulanıyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "uç hazır olunca" in result["error"]


# ============================================================== denetim izi

async def test_her_calisma_gerekcesiyle_yerel_deftere_yazilir() -> None:
    service, api, store = _service()
    await _run(service, api)
    kayit = next(iter(store.runs.values()))
    assert kayit["reason"] == "Açıklamalar zenginleştiriliyor"
    assert kayit["actor"] == "Ali"
    assert kayit["cost"] == 150


async def test_calisma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store = _service()
    api.fail.add("bbd_ai_run")
    await service.run_tool("product_description", target_ids=[5], params={},
                           reason="Açıklama üretimi deneniyor", actor="Ali", dry_run=False)
    assert any(row["status"] == "failed" for row in store.runs.values())
    assert any(row["result"] == "hata" for row in store.audit)


async def test_gecmis_yerel_defterle_magaza_kaydini_birlestirir() -> None:
    service, api, _ = _service()
    await _run(service, api)
    api.runs_payload = {"items": [{"id": 99, "tool": "seo_meta",
                                   "created_at": "2030-01-01T00:00:00"}], "meta": {}}
    result = await service.history()
    assert result["total"] == 2
    assert result["items"][0]["local"] is False


# ================================================================= ayarlar

async def test_tanimayan_ayar_anahtari_sessizce_yazilmaz() -> None:
    service, _, store = _service()
    result = await service.save_settings(values={"monthly_budget": 25_000, "uydurma": 1},
                                         reason="Bütçe yükseltiliyor", actor="Ali")
    assert result["skipped"] == ["uydurma"]
    assert store.prefs["monthly_budget"] == "25000"


async def test_ekran_tercihi_config_varsayilanini_ezer() -> None:
    service, _, _ = _service(monthly_budget=1_000)
    await service.save_settings(values={"monthly_budget": 9_000},
                                reason="Bütçe yükseltiliyor", actor="Ali")
    settings = await service.settings()
    assert settings["values"]["monthly_budget"] == 9_000
    assert settings["sources"]["monthly_budget"] == "ekran"


async def test_tanimayan_arac_kapatma_listesine_girmez() -> None:
    service, _, store = _service()
    await service.save_settings(values={"disabled_tools": ["seo_meta", "uydurma_arac"]},
                                reason="Araçlar kapatılıyor", actor="Ali")
    assert store.prefs["disabled_tools"] == "seo_meta"


# =========================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_bastirilmaz() -> None:
    # Serbest yol kabul etmek `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    service, _, _ = _service()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False


async def test_yazici_yoksa_ekran_bunu_soyler() -> None:
    service, _, _ = _service()
    status = await service.printer_status()
    assert status["ready"] is False
    assert "Yazıcı" in status["error"]


# =========================================================== zaman damgası

def test_yerel_defter_utc_degil_yerel_saat_yazar() -> None:
    # PARA KAPISI BUNA BAĞLI. `analytics.ledger_spend` ayı `created_at[:7]` ile
    # kesip `rules.today_iso()`den gelen YEREL ayla karşılaştırıyor; UTC yazan
    # sürümde +14 saatlik bir dilimde ayın ilk saatlerinde yapılan çalıştırma
    # bir ÖNCEKİ ayın bütçesine işleniyordu. Denetim listesindeki saat de
    # olduğu gibi basıldığı için her satır saatlerce erken görünüyordu.
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati"          # UTC+14 — UTC ile aynı olamaz
    time.tzset()
    try:
        stamp = service_module._now()
        # Ölçüt `time.localtime`dır, `datetime` DEĞİL: sınanan kod da datetime
        # kullanıyor, ikisini karşılaştırmak kendini doğrulamak olurdu.
        assert stamp[:13] == time.strftime("%Y-%m-%dT%H", time.localtime())
        assert analytics.month_key(stamp) == analytics.month_key(rules.today_iso())
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


# ====================================================== salt okunur çıktı

async def test_yazmayan_aracin_ozeti_saklanir_ve_gecmisten_okunur() -> None:
    # Yorum duygu özetinin fark tablosu YOKTUR; çıktının tamamı bu metindir.
    # Saklanmadığı sürüm geçmişte bomboş bir çekmece açıyordu: ne tablo ne metin.
    service, api, _ = _service()
    api.run_payload = {"runId": 7, "tokens": 120, "cost": "0.40",
                       "summary": "Yorumların çoğu kargo hızından memnun."}
    run = await service.run_tool("review_sentiment", target_ids=[], params={"days": 30},
                                 reason="Yorum eğilimi inceleniyor", actor="Ali", dry_run=False)
    assert run["text"] == "Yorumların çoğu kargo hızından memnun."

    detail = await service.run_detail(run["token"])
    assert detail["text"] == "Yorumların çoğu kargo hızından memnun."
    assert detail["readOnly"] is True


# ============================================================ susturma listesi

async def test_susturulmus_bulgular_ayarlardan_okunur_tarama_gerektirmez() -> None:
    # Ayarlar sekmesindeki "geri al" düğmesi bu listeyi okuyor. Liste yalnız
    # `catalog()` yanıtında dursaydı Katalog Sağlığı sekmesi hiç açılmadan
    # susturma geri alınamaz, geri alma sonrası da satır ekranda kalırdı.
    service, api, _ = _service()
    await service.mute(finding_key="no_image:1", kind="no_image", target_id=1,
                       reason="Görsel tedarikçiden bekleniyor", actor="Ali")
    settings = await service.settings()
    assert [item["key"] for item in settings["muted"]] == ["no_image:1"]
    assert api.used("products") == []                 # tarama tetiklenmedi


async def test_susturma_kalkinca_ayarlar_listesinden_dusru() -> None:
    service, _, _ = _service()
    await service.mute(finding_key="no_image:1", kind="no_image", target_id=1,
                       reason="Görsel tedarikçiden bekleniyor", actor="Ali")
    await service.unmute(finding_key="no_image:1", reason="Görsel yüklendi, tekrar bakılsın",
                         actor="Ali")
    assert (await service.settings())["muted"] == []


# ================================================================ dışa aktarma

async def test_csv_onem_sutunu_turkce_yazar(tmp_path: Path) -> None:
    # Kural anahtarı (`high`) İngilizce ve ASCII'dir çünkü kod tanımlayıcısıdır;
    # CSV kullanıcıya gider ve "high" yazması `disabledRules` çevrilirken
    # gözden kaçmıştı.
    service, api, _ = _service(export_path=str(tmp_path))
    api.list_payload = {"items": [product(1, images=[])], "meta": {}, "truncated": False}
    result = await service.export_csv()
    assert result["ok"] is True
    written = Path(result["path"]).read_bytes().decode("utf-8-sig")
    assert "Ağır" in written
    assert "high" not in written
