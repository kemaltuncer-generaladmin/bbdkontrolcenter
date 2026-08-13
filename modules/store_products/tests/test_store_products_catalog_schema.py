"""Görsel yükleme ve nitelik/aile SERVİS kuralları. Ağ taklit edilir.

Saf dönüşümlerin testi ayrı dosyalarda (`..._images`, `..._schema`); burada
servisin doğru anda doğru şeyi yapıp yapmadığına bakılır: reddi mağazaya
göndermemek, gerekçe istemek, kilitli alanı durdurmak, kullanımdaki niteliği
silmemek, aile düzenini istenmeden değiştirmemek.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path
from typing import Any

from store_products_backend.service import ProductsService
from store_products_fakes import FakeApi, FakeLog, FakeStore

URUN = {"id": 5, "sku": "KLM-1", "type": "simple", "status": 1, "name": "Kalem",
        "images": [{"id": 3, "url": "https://x/1.jpg"}]}

YAYINEVI = {"id": 33, "code": "publisher", "type": "select", "adminName": "Yayınevi",
            "isRequired": 1, "isFilterable": 1, "isVisibleOnFront": 1, "isUserDefined": 1,
            "options": [{"id": 10, "adminName": "ACİL", "sortOrder": 1}]}
DESI = {"id": 39, "code": "desi", "type": "text", "adminName": "Desi", "isUserDefined": 1}
SKU_NITELIK = {"id": 1, "code": "sku", "type": "text", "adminName": "Stok Kodu",
               "isRequired": 1, "isUserDefined": 0}

KITAP = {"id": 2, "code": "kitap", "name": "Kitap", "attributeGroups": [
    {"id": 10, "code": "general", "name": "Genel", "column": 1, "position": 0,
     "attributes": [{"id": 1, "code": "sku", "type": "text", "isRequired": 1},
                    {"id": 33, "code": "publisher", "type": "select", "isRequired": 1}]},
]}


def _png(width: int, height: int) -> str:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(header)) + b"IHDR" + header
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + header))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk).decode("ascii")


def _service(**config: Any) -> tuple[ProductsService, FakeApi, FakeStore]:
    api = FakeApi({5: dict(URUN)})
    api.attributes_payload = {"items": [dict(YAYINEVI), dict(DESI), dict(SKU_NITELIK)],
                              "meta": {}}
    api.attributes_by_id = {33: dict(YAYINEVI), 39: dict(DESI), 1: dict(SKU_NITELIK)}
    api.families_payload = {"items": [{"id": 2, "code": "kitap", "name": "Kitap",
                                       "attributeGroups": None}], "meta": {}}
    api.families_by_id = {2: dict(KITAP)}
    api.family_totals = {2: 1420}
    store = FakeStore()
    service = ProductsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# =========================================================== görsel yükleme

async def test_gerekce_kisa_ise_dosya_magazaya_HIC_gitmez() -> None:
    service, api, _ = _service()
    result = await service.upload_image(5, filename="k.png", content=_png(900, 900),
                                        reason="kısa", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("upload_product_image") == []


async def test_reddedilen_dosya_magazaya_gonderilmez_ama_denetim_izine_girer() -> None:
    service, api, store = _service()
    result = await service.upload_image(5, filename="katalog.pdf",
                                        content=base64.b64encode(b"%PDF-1.7 ").decode(),
                                        reason="Kapak görseli ekleniyor", actor="Ali",
                                        dry_run=False)
    assert result["ok"] is False
    assert result["rejected"] is True
    assert "PDF" in result["error"]
    assert api.used("upload_product_image") == []       # hız kovasından pay bile harcanmadı
    # "Bu görsel neden yüklenmedi" sorusunun cevabı ekran kapandıktan sonra da durmalı.
    assert store.audit[-1]["result"] == "reddedildi"


async def test_ayar_sinirini_asan_dosya_reddedilir() -> None:
    service, api, _ = _service(image_max_mb=1)
    büyük = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024)).decode()
    result = await service.upload_image(5, filename="b.png", content=büyük,
                                        reason="Kapak görseli ekleniyor", actor="Ali")
    assert result["ok"] is False
    assert "MB" in result["error"]
    assert api.used("upload_product_image") == []


async def test_ayar_sunucu_sinirini_YUKARI_cekemez() -> None:
    # Ayar 32 MB dese de sunucu 4 MB'ın üstünü 422 ile reddediyor.
    service, _, _ = _service(image_max_mb=32)
    assert service.image_rules()["maxBytes"] == 4 * 1024 * 1024


async def test_kucuk_gorsel_yuklenir_ama_uyari_dondurulur() -> None:
    service, api, _ = _service()
    result = await service.upload_image(5, filename="k.png", content=_png(320, 240),
                                        reason="Kapak görseli ekleniyor", actor="Ali",
                                        dry_run=False)
    assert result["ok"] is True
    assert result["width"] == 320
    assert any("320×240" in note for note in result["warnings"])
    assert api.used("upload_product_image")[0]["dry_run"] is False


async def test_yuklemede_duzeltilen_dosya_adi_gecide_gider() -> None:
    service, api, _ = _service()
    await service.upload_image(5, filename="kapak.jpg", mime="image/jpeg",
                               content=_png(900, 900), reason="Kapak görseli ekleniyor",
                               actor="Ali", dry_run=False)
    gonderim = api.used("upload_product_image")[0]
    assert gonderim["filename"] == "kapak.png"
    assert gonderim["mime"] == "image/png"


async def test_magaza_patlarsa_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("upload_product_image")
    result = await service.upload_image(5, filename="k.png", content=_png(900, 900),
                                        reason="Kapak görseli ekleniyor", actor="Ali")
    assert result["ok"] is False
    assert "patladı" in result["error"]


async def test_gorsel_listesi_tek_istekle_tazelenir() -> None:
    service, api, _ = _service()
    result = await service.images(5)
    assert result["images"][0]["cover"] is True
    assert result["rules"]["maxBytes"] > 0
    assert len(api.used("product")) == 1


# ================================================================== nitelik

async def test_nitelik_listesi_kullanim_sayisiyla_gelir() -> None:
    service, _, _ = _service()
    result = await service.attributes()
    rows = {row["code"]: row for row in result["items"]}
    assert rows["publisher"]["usageProducts"] == 1420
    assert rows["publisher"]["usageFamilies"] == ["Kitap"]
    # Hiçbir ailede olmayan nitelik: kullanım 0 değil, hiç kayıt yok → None.
    assert rows["desi"]["usageProducts"] is None


async def test_aileler_okunamazsa_liste_yine_gelir_kullanim_bos_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("families")
    result = await service.attributes()
    assert result["connected"] is True
    assert len(result["items"]) == 3
    assert result["items"][0]["usageProducts"] is None
    assert any("Aileler okunamadı" in note for note in result["warnings"])


async def test_kullanilmayan_cipi_bilinmeyeni_icermez() -> None:
    service, _, _ = _service()
    result = await service.attributes(scope="unused")
    # `desi` kullanımı BİLİNMİYOR (hiçbir ailede yok, sayım da yapılmadı):
    # "kullanılmıyor" saymak silme kapısını yanlış açardı.
    assert result["items"] == []


async def test_nitelik_arama_kod_ve_adda_calisir() -> None:
    service, _, _ = _service()
    assert [row["code"] for row in (await service.attributes(q="publ"))["items"]] == ["publisher"]
    assert [row["code"] for row in (await service.attributes(q="yayın"))["items"]] == ["publisher"]


async def test_kod_degistirme_denemesi_reddedilir_ve_istek_gitmez() -> None:
    service, api, _ = _service()
    result = await service.update_attribute(33, patch={"code": "yayinevi", "name": "Yeni"},
                                            reason="Kod düzeltiliyor sanılıyor", actor="Ali",
                                            dry_run=False)
    assert result["ok"] is False
    assert "değiştirilemez" in result["error"]
    assert api.used("update_attribute") == []


async def test_guncellemede_kod_ve_tip_govdeye_konmaz() -> None:
    service, api, _ = _service()
    result = await service.update_attribute(33, patch={"name": "Yayın Evi", "filterable": False},
                                            reason="Ad Türkçeleştiriliyor", actor="Ali",
                                            dry_run=False)
    assert result["ok"] is True
    payload = api.used("update_attribute")[0]["payload"]
    assert payload == {"admin_name": "Yayın Evi", "is_filterable": 0}


async def test_yeni_nitelikte_rezerve_kod_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.create_attribute(code="type", kind="text", patch={"name": "Tip"},
                                            reason="Yeni nitelik açılıyor", actor="Ali")
    assert result["ok"] is False
    assert api.used("create_attribute") == []


async def test_yeni_nitelik_hicbir_ailede_olmadigi_soylenir() -> None:
    service, _, _ = _service()
    result = await service.create_attribute(code="raf_kodu", kind="text",
                                            patch={"name": "Raf kodu"},
                                            reason="Depo rafı için nitelik", actor="Ali",
                                            dry_run=False)
    assert result["ok"] is True
    assert "HİÇBİR AİLEDE" in result["notice"]


async def test_kullanimdaki_nitelik_silinmez_ve_istek_gitmez() -> None:
    service, api, store = _service()
    result = await service.delete_attribute(33, reason="Artık kullanılmıyor sanıldı",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "1420" in result["error"]
    assert "PASİFLEŞTİRİN" in result["error"]
    assert api.used("delete_attribute") == []
    assert store.audit[-1]["result"] == "reddedildi"


async def test_kullanimi_bilinmeyen_nitelik_de_silinmez() -> None:
    service, api, _ = _service()
    api.fail.add("families")
    result = await service.delete_attribute(39, reason="Temizlik yapılıyor burada",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("delete_attribute") == []


async def test_hicbir_ailede_olmayan_nitelik_silinebilir() -> None:
    service, api, _ = _service()
    api.families_by_id[2] = {"id": 2, "name": "Kitap", "attributeGroups": []}
    result = await service.delete_attribute(39, reason="Hiç kullanılmadı, kaldırılıyor",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert len(api.used("delete_attribute")) == 1


async def test_bir_aile_okunamazsa_bos_kullanim_silme_kapisini_ACMAZ() -> None:
    # `test_kullanimi_bilinmeyen...` ile aynı görünen ama BAŞKA bir durum:
    # aile listesi geldi, ailenin DETAYI gelmedi. Kullanım listesi yine boş
    # çıkar; okunamayan aile niteliği taşıyor olabilir, silme açılmamalı.
    service, api, _ = _service()
    api.families_by_id.pop(2)
    result = await service.delete_attribute(39, reason="Hiç kullanılmadı, kaldırılıyor",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("delete_attribute") == []


async def test_magaza_409_dondurunce_ne_yapilacagi_yazilir() -> None:
    # Geçidin ham metni ("çakışma yüzünden reddetti") kullanıcıya ne yapacağını
    # söylemiyor. Ekranın aile listesi 900 sn önbellekli; nitelik bu arada bir
    # aileye eklenmiş olabilir ve mağaza son sözü söyler.
    service, api, _ = _service()
    api.families_by_id[2] = {"id": 2, "name": "Kitap", "attributeGroups": []}
    api.fail.add("delete_attribute")
    api.fail_codes["delete_attribute"] = "conflict"
    result = await service.delete_attribute(39, reason="Hiç kullanılmadı, kaldırılıyor",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "ailesinde" in result["error"]
    assert "PASİFLEŞTİRİN" in result["error"]
    assert "çakışma" not in result["error"]


async def test_kodsuz_istisna_409_metnine_cevrilmez() -> None:
    # `code` taşımayan bir arıza "ailede kullanılıyor" diye anlatılamaz:
    # yanlış teşhis, kullanıcıyı olmayan bir aileyi temizlemeye gönderirdi.
    service, api, _ = _service()
    api.families_by_id[2] = {"id": 2, "name": "Kitap", "attributeGroups": []}
    api.fail.add("delete_attribute")
    result = await service.delete_attribute(39, reason="Hiç kullanılmadı, kaldırılıyor",
                                            actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "patladı" in result["error"]


async def test_pasiflestirme_silme_ucuna_HIC_dokunmaz() -> None:
    service, api, _ = _service()
    result = await service.deactivate_attribute(33, reason="Vitrinden kaldırılıyor şimdilik",
                                                actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("delete_attribute") == []
    assert api.used("update_attribute")[0]["payload"] == {
        "is_visible_on_front": 0, "is_filterable": 0, "is_required": 0}
    assert "geri alınabilir" in result["notice"]


async def test_secenek_silmek_gerekce_ister() -> None:
    service, api, _ = _service()
    result = await service.delete_option(33, 10, reason="kısa", actor="Ali")
    assert result["ok"] is False
    assert api.used("delete_attribute_option") == []


# ==================================================================== aile

async def test_aile_listesi_urun_sayisiyla_gelir() -> None:
    service, _, _ = _service()
    result = await service.families()
    assert result["items"][0]["productCount"] == 1420
    assert result["items"][0]["groupCount"] is None      # liste ucu grupları vermiyor


async def test_aile_kunyesinde_havuz_ve_cekirdek_kodlar_bulunur() -> None:
    service, _, _ = _service()
    result = await service.family(2)
    assert result["family"]["groups"][0]["attributes"][0]["code"] == "sku"
    assert [row["code"] for row in result["pool"]] == ["desi", "publisher", "sku"]
    assert "sku" in result["coreCodes"]


async def test_gruplar_verilmezse_duzen_govdeye_KONMAZ() -> None:
    service, api, _ = _service()
    result = await service.save_family(2, name="Kitaplar", groups=None,
                                       reason="Aile adı düzeltiliyor", actor="Ali",
                                       dry_run=False)
    assert result["ok"] is True
    assert result["touchedGroups"] is False
    assert api.used("update_family")[0]["payload"] == {"name": "Kitaplar"}


async def test_cekirdek_nitelik_dusuren_duzen_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.save_family(2, name="Kitap", groups=[
        {"code": "general", "name": "Genel", "column": 1, "position": 0,
         "attributeIds": [33]},
    ], reason="Grup sadeleştiriliyor", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "sku" in result["error"]
    assert api.used("update_family") == []


async def test_nitelik_listesi_okunamazsa_aile_duzeni_kaydedilmez() -> None:
    # Gönderilen liste mağazadaki düzenin YERİNE geçiyor; doğrulayamadan
    # kaydetmek grupları sessizce silebilir.
    service, api, _ = _service()
    api.fail.add("attributes")
    result = await service.save_family(2, name="Kitap", groups=[
        {"name": "Genel", "attributeIds": [1]}], reason="Düzen değiştiriliyor",
        actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("update_family") == []
