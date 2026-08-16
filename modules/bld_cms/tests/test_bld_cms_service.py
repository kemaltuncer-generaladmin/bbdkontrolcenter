"""İş kurallarının testi — ağa çıkmaz, `FakeApi` ve `FakeStore` kullanır.

Dört iddia bu dosyanın omurgasıdır:

 1. HER YAZMA `dry_run=` BAYRAĞINI AÇIKÇA GEÇİRİR. Geçidin `config/local.yaml`
    dosyası git dışıdır ve orada `dry_run_default: true` yazıyor olabilir;
    bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır ve ekran
    "kaydedildi" der.
 2. GEÇİT DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma `ok: True, connected: False`
    döner, istisna sızmaz.
 3. YEREL İZ GEÇİT ÇAĞRISINDAN ÖNCE DÜŞER. Ağ koparsa geriye yalnız o kalır.
 4. SÜRÜM SATIRI YALNIZ GERÇEK YAZMADAN SONRA yazılır ve "önceki hâl"i TAZE
    okumadan alır — panelin elindeki eski kopyadan değil.
"""

from __future__ import annotations

import json

from bld_cms_backend.service import CmsService
from bld_cms_fakes import FakeApi, FakeLog, FakeStore

GEREKCE = "İletişim telefonu güncellendi"
AKTOR = "Ayşe Yılmaz"


def build(**config: object) -> tuple[CmsService, FakeApi, FakeStore]:
    api = FakeApi()
    store = FakeStore()
    service = CmsService(api=api, store=store, log=FakeLog(), config=dict(config))
    return service, api, store


# ==================================================================== okuma

async def test_icerik_okunur_ve_yedi_anahtar_doner() -> None:
    service, _api, _store = build()
    out = await service.content()
    assert out["ok"] is True
    assert out["connected"] is True
    assert [row["key"] for row in out["items"]] == list(out["keys"])
    assert len(out["items"]) == 7


async def test_gecit_duserse_okuma_ekrani_ayakta_tutar() -> None:
    # K7: `ok` UCUN SAĞLIĞINI anlatır, okumanın başarısını değil. Ayrımı
    # `connected` taşır; yalnız `ok`a bakan bir panel "kayıt yok" derdi.
    service, api, _store = build()
    api.fail = {"site_content", "site_services", "site_posts"}

    for call in (service.content(), service.services(), service.posts()):
        out = await call
        assert out["ok"] is True
        assert out["connected"] is False
        assert out["error"]
        assert out["items"] == []
        # Ekran sözleşmesi YERELDİR: geçit düşse bile form ve sınırlar çizilir.
        assert out["screen"]["limits"]["reason_min"] == 10


async def test_gorsel_dugmesi_ucu_yokken_hic_cizilmez() -> None:
    # Kitin kuralı: bir düğme ya çalışır ya hiç çizilmez. Sözleşmede görsel
    # yükleme ucu yok; ekran nedenini YAZAR ve düğmeyi çizmez.
    service, _api, _store = build()
    screen = service.screen()
    assert screen["image_upload"]["available"] is False
    assert "Kaynak" in screen["image_upload"]["reason"]

    out = await service.upload_image(content="x", filename="a.png", reason=GEREKCE,
                                     actor=AKTOR, dry_run=False)
    assert out["ok"] is False
    assert out["code"] == "control_endpoint_missing"


async def test_ekran_sozlesmesi_izin_listesini_tasir() -> None:
    # Panel izin verilen etiketleri kullanıcıya yazıyor; listeyi kendi
    # tutsaydı sunucudaki liste değiştiğinde yanlış cümleyi gösterirdi.
    service, _api, _store = build()
    screen = service.screen()
    assert "strong" in screen["editor"]["allowed_tags"]
    assert "script" not in screen["editor"]["allowed_tags"]
    assert screen["revalidate_default"] is True
    assert json.dumps(screen, ensure_ascii=False)


# ============================================================== içerik yazma

async def test_icerik_yazmada_dry_run_ACIKCA_gecilir() -> None:
    service, api, _store = build()
    out = await service.save_content("contact", value={"phone": "3124445577"},
                                     reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    cagri = api.kwargs("set_site_content")
    # Bayrak VARSAYILANA BIRAKILMAZ: `None` geçmek, geçidin git dışı ayarına
    # güvenmek olurdu.
    assert cagri["dry_run"] is False
    assert cagri["actor"] == AKTOR
    assert cagri["reason"] == GEREKCE


async def test_degismemis_deger_sunucuya_hic_gitmez() -> None:
    service, api, _store = build()
    mevcut = {"phone": "3124445566", "email": "info@bld.example",
              "address": "Kızılırmak Mah. 1443. Cad. No:12, Çankaya / Ankara",
              "working_hours": "Hafta içi 08:00 – 18:00"}
    out = await service.save_content("contact", value=mevcut, reason=GEREKCE,
                                     actor=AKTOR)
    assert out["ok"] is True
    assert out["changed"] is False
    assert "set_site_content" not in api.names()


async def test_uydurma_anahtar_reddedilir_istek_gitmez() -> None:
    service, api, _store = build()
    out = await service.save_content("uydurma", value={}, reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert api.names() == []


async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9 — çift kapı: arayüzde zorunlu göstermek, istemcinin gövdeyi elle
    # kurmasını engellemez.
    service, api, _store = build()
    out = await service.save_content("brand", value={"name": "X"}, reason="kısa",
                                     actor=AKTOR)
    assert out["ok"] is False
    assert api.names() == []


async def test_iz_gecit_cagrisindan_ONCE_duser() -> None:
    # Ağ koparsa geriye YALNIZ bu satır kalır: "kim neyi denedi".
    service, api, store = build()
    api.fail = {"set_site_content"}
    out = await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                                     actor=AKTOR)
    assert out["ok"] is False
    assert store.actions() == [("cms.content.update", "denendi"),
                               ("cms.content.update", "hata")]
    # Yazma başarısız: sürüm satırı YOK. Olsaydı, olmamış bir değişikliği
    # geçmişe geçirmiş olurduk.
    assert store.revisions == []


async def test_kuru_provada_uzaga_yazma_gitmez_ve_surum_yazilmaz() -> None:
    service, api, store = build()
    out = await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                                     actor=AKTOR, dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert "set_site_content" not in api.names()
    assert store.actions()[-1] == ("cms.content.update", "dry_run")
    assert store.revisions == []


async def test_surum_satiri_onceki_hali_TAZE_okumadan_alir() -> None:
    service, _api, store = build()
    yeni = {"name": "BLD Catering", "tagline": "Yeni slogan"}
    out = await service.save_content("brand", value=yeni, reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True

    assert len(store.revisions) == 1
    satir = store.revisions[0]
    assert satir["target_type"] == "site_content"
    assert satir["target_key"] == "brand"
    assert json.loads(satir["before_json"])["tagline"] == "Kurumsal mutfak çözümleri"
    assert json.loads(satir["after_json"])["tagline"] == "Yeni slogan"
    # Sunucunun denetim satırı kimliği taşınır: iki izi yan yana koymanın
    # tek yolu budur.
    assert satir["audit_id"] == 2201


async def test_buyuk_govde_kirpilmaz_kunyeye_duser() -> None:
    # Kesilmiş bir metni "eski hâl" diye saklamak, geri getirildiğinde yarım
    # bir sayfa üretirdi.
    service, api, store = build(revision_max_bytes=1024)
    api.content["data"]["company"] = {"value": {"about": "x" * 5000},
                                      "updated_at": "2026-08-02T10:00:00Z"}
    out = await service.save_content("company", value={"about": "y" * 5000},
                                     reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    satir = store.revisions[0]
    assert satir["truncated"] == 1
    assert json.loads(satir["before_json"])["_truncated"] is True


async def test_boyut_siniri_asan_deger_gonderilmez() -> None:
    service, api, _store = build()
    out = await service.save_content("company", value={"x": "y" * (256 * 1024)},
                                     reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert "set_site_content" not in api.names()


async def test_sekil_uyusmazligi_yazmayi_engellemez_uyari_doner() -> None:
    service, _api, _store = build()
    out = await service.save_content("faq", value={"q": "soru"}, reason=GEREKCE,
                                     actor=AKTOR)
    assert out["ok"] is True
    kodlar = {item.get("code") for item in out["warnings"]}
    assert "shape_mismatch" in kodlar


# ================================================================= hizmetler

async def test_ayni_adres_engellenir_ve_hangi_kayit_oldugu_soylenir() -> None:
    # Sunucu 409 döndürüyor ama o hata kullanıcıya "CONFLICT" diye ulaşır.
    service, api, store = build()
    out = await service.create_service(
        fields={"slug": "kurumsal-catering", "title": "Kopya"},
        reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert "Kurumsal Catering" in out["error"]
    assert "create_site_service" not in api.names()
    assert store.actions()[-1] == ("cms.service.create", "engellendi")


async def test_hizmet_govdesi_kayitta_temizlenir_ve_geri_okunan_gosterilir() -> None:
    # Gönderdiğini geri okumayan bir editör, yapıştırmanın kaybolduğunu fark
    # ettirmez.
    service, api, _store = build()
    api.sanitize_body = "<p>Temiz</p>"
    out = await service.create_service(
        fields={"slug": "etkinlik-catering", "title": "Etkinlik Catering",
                "body_html": "<p>Temiz</p><script>alert(1)</script>"},
        reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    # Geçide giden gövdede `script` YOK: ilk kapı burada.
    gonderilen = api.kwargs("create_site_service")["fields"]["body_html"]
    assert "script" not in gonderilen
    assert out["data"]["body_html"] == "<p>Temiz</p>"


async def test_bos_kismi_guncelleme_reddedilir() -> None:
    # Yalnız `reason` taşıyan bir `PATCH`, hiçbir şey değiştirmeden denetim
    # izine satır yazardı.
    service, api, _store = build()
    out = await service.update_service(3, fields={}, reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert "update_site_service" not in api.names()


async def test_adres_degisimi_uyari_uretir() -> None:
    service, _api, _store = build()
    out = await service.update_service(3, fields={"slug": "kurumsal-yemek"},
                                       reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    kodlar = {item.get("code") for item in out["warnings"]}
    assert "slug_changed" in kodlar


async def test_bilinmeyen_alan_govdeden_dusurulur() -> None:
    # Laravel tanımadığı alanı sessizce yok sayar; "kaydedildi" diyen bir
    # ekranın arkasında hiçbir yere yazılmamış bir değer bırakmak, açık bir
    # hatadan çok daha pahalıdır.
    service, api, _store = build()
    out = await service.update_service(3, fields={"title": "Yeni", "uydurma": 1},
                                       reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    assert set(api.kwargs("update_site_service")["fields"]) == {"title"}


async def test_yetkisiz_silme_istegi_hic_gonderilmez() -> None:
    # Çift kapı (K9): uçtaki `requires` ve buradaki `allow_delete`.
    service, api, store = build()
    out = await service.delete_service(3, reason=GEREKCE, actor=AKTOR,
                                       allow_delete=False)
    assert out["ok"] is False
    assert "bld_cms.delete" in out["error"]
    assert "delete_site_service" not in api.names()
    assert store.actions() == [("cms.service.delete", "engellendi")]


async def test_silinen_kaydin_son_hali_gecmise_yazilir() -> None:
    service, _api, store = build()
    out = await service.delete_service(3, reason="Bu hizmet artık verilmiyor",
                                       actor=AKTOR, allow_delete=True)
    assert out["ok"] is True
    satir = store.revisions[0]
    assert satir["action"] == "cms.service.delete"
    assert json.loads(satir["before_json"])["slug"] == "kurumsal-catering"
    # Silmede "sonraki hâl" YOKTUR ve sıfır uydurulmaz.
    assert json.loads(satir["after_json"]) is None


# =================================================================== yazılar

async def test_bos_govdeli_yazi_gonderilmez() -> None:
    service, api, _store = build()
    out = await service.create_post(
        fields={"slug": "bos", "title": "Boş", "body_html": "<p> </p>"},
        reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert "create_site_post" not in api.names()


async def test_okuma_suresi_bos_birakilabilir_sifira_dusmez() -> None:
    # Boş "sen hesapla" demektir; sıfır yazmak "bu yazı okunmuyor" anlamına
    # gelen bir sayı üretirdi.
    service, api, _store = build()
    out = await service.update_post(21, fields={"reading_minutes": ""},
                                    reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    assert api.kwargs("update_site_post")["fields"]["reading_minutes"] is None


async def test_bulunamayan_yazi_icin_yazma_denenmez() -> None:
    # Panelin elindeki eski satır "taze" sayılmaz: o zaman üzerine
    # yazdığımız şeyin ne olduğunu bilmezdik.
    service, api, _store = build()
    out = await service.update_post(999, fields={"title": "Yeni"}, reason=GEREKCE,
                                    actor=AKTOR)
    assert out["ok"] is False
    assert "update_site_post" not in api.names()


# ============================================================ yeniden çizdirme

async def test_tazeleme_basarisizsa_yazma_yine_basarilidir_ama_soylenir() -> None:
    # Sunucu bilerek 200 döndürüyor: içerik gerçekten kaydedildi. Ekran bunu
    # söylemezse yönetici "kaydettim ama sitede yok" der ve aynı kaydı ikinci
    # kez yazar.
    service, api, store = build()
    api.revalidate_status = "failed"
    out = await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                                     actor=AKTOR)
    assert out["ok"] is True
    assert out["revalidate"]["status"] == "failed"
    assert "YAZILDI" in out["revalidate"]["note"]
    # Kayıt yazıldı: sürüm satırı DA yazılır.
    assert len(store.revisions) == 1


async def test_tazeleme_kapatilinca_uzaga_bayrak_kapali_gider() -> None:
    service, api, _store = build()
    out = await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                                     actor=AKTOR, revalidate=False)
    assert out["ok"] is True
    assert api.kwargs("set_site_content")["revalidate"] is False
    assert out["revalidate"]["status"] == "skipped"


async def test_toplu_cizdirme_hatasi_izde_hata_olarak_durur() -> None:
    service, api, store = build()
    api.revalidate_status = "failed"
    out = await service.revalidate(paths=["/hizmetler"], reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is True
    assert out["revalidate"]["status"] == "failed"
    assert store.actions()[-1] == ("cms.revalidate", "hata")
    assert api.kwargs("revalidate_site")["dry_run"] is False


async def test_bozuk_yol_listesi_gonderilmez() -> None:
    service, api, _store = build()
    out = await service.revalidate(paths=["hizmetler"], reason=GEREKCE, actor=AKTOR)
    assert out["ok"] is False
    assert api.names() == []


# =============================================================== yerel geçmiş

async def test_gecmis_okumasi_aga_cikmaz() -> None:
    # BLD düşse bile "dün ne yazıyordu" sorusu cevaplanabilmeli.
    service, api, _store = build()
    await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                               actor=AKTOR)
    api.calls.clear()

    out = await service.revisions(target_type="site_content", target_key="brand")
    assert out["ok"] is True
    assert len(out["items"]) == 1
    assert api.names() == []


async def test_kirpilmis_surum_duzenleyiciye_getirilemez() -> None:
    service, api, store = build(revision_max_bytes=1024)
    api.content["data"]["company"] = {"value": {"about": "x" * 5000},
                                      "updated_at": None}
    await service.save_content("company", value={"about": "y" * 5000},
                               reason=GEREKCE, actor=AKTOR)

    out = await service.revision(store.revisions[0]["id"])
    assert out["ok"] is True
    assert out["restorable"] is False


async def test_iz_yazilamazsa_is_durmaz() -> None:
    # K7: denetim satırı yazılamadı diye içerik yazılmadan bırakılmaz.
    service, api, store = build()
    store.broken = True
    out = await service.save_content("brand", value={"name": "Yeni"}, reason=GEREKCE,
                                     actor=AKTOR)
    assert out["ok"] is True
    assert "set_site_content" in api.names()
