"""Yapılandırma sekmesi — beyaz liste, kaynak rozeti ve yazma kapıları.

Ağa çıkmaz. Ağaç `FakeApi.menu_tree` ile kurulur; kodlar canlıdan (2026-08-13)
öğrenilen gerçek kodlardır, uydurma değildir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_dashboard_backend import config_map
from store_dashboard_backend.service import DashboardService
from store_dashboard_fakes import FakeApi, FakeLog, FakeStore, menu_node

GUEST = "sales.checkout.shopping_cart.allow_guest_checkout"
THRESHOLD = "catalog.inventory.stock_options.out_of_stock_threshold"
KT_ACTIVE = "sales.payment_methods.kuveytturk.active"
KT_TITLE = "sales.payment_methods.kuveytturk.title"
KT_PASSWORD = "sales.payment_methods.kuveytturk.password"
STORE_NAME = "sales.shipping.origin.store_name"

REASON = "Kapıda ödeme kapatıldı, kurye anlaşması bitti."


def live_tree() -> list[dict[str, Any]]:
    """Canlıdaki ağacın testlik minimumu — kodlar ve tipler gerçek."""
    return [
        menu_node("sales", [], [
            menu_node("sales.checkout.shopping_cart", [
                {"name": "allow_guest_checkout", "type": "boolean",
                 "title": "Misafir ödemesine izin ver", "default": 1, "value": "1"},
            ]),
            menu_node("sales.payment_methods.kuveytturk", [
                {"name": "active", "type": "boolean", "title": "Durum", "value": "1"},
                {"name": "title", "type": "text", "title": "Başlık",
                 "value": "Kredi/Banka Kartı ile Öde"},
                {"name": "password", "type": "password", "title": "API Kullanıcı Şifresi",
                 "default": "enc:v1:gizli", "value": "enc:v1:gizli"},
                {"name": "hash_check", "type": "select", "title": "İmza Doğrulama Modu",
                 "value": "enforce"},
            ]),
            menu_node("sales.shipping.origin", [
                {"name": "store_name", "type": "text", "title": "Mağaza Adı"},
            ]),
        ]),
        menu_node("catalog.inventory.stock_options", [
            {"name": "out_of_stock_threshold", "type": "number",
             "title": "Stokta Olmayan Eşik Değeri", "default": "0", "value": "0"},
        ]),
    ]


def _service(api: FakeApi | None = None) -> tuple[DashboardService, FakeApi, FakeStore]:
    api = api or FakeApi([])
    api.menu_tree = live_tree()
    store = FakeStore()
    service = DashboardService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr"},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


def field_of(result: dict[str, Any], code: str) -> dict[str, Any]:
    for group in result["groups"]:
        for field in group["fields"]:
            if field["code"] == code:
                return field
    raise AssertionError(f"{code} beyaz listede yok")


# ==================================================== saf harita — config_map

def test_slug_koddan_turetilmez_dugum_anahtarindan_okunur() -> None:
    # `sales.checkout.shopping_cart.allow_guest_checkout` içinde slug ÜÇ
    # parçalı; koddan "son noktadan önceki" kuralıyla çıkarmak da doğru sonuç
    # verirdi ama `reorder.admin` gibi alanlarda yanılırdı. Kaynak düğümdür.
    declared = config_map.declared_fields(live_tree())
    assert declared[GUEST]["slug"] == "sales.checkout.shopping_cart"
    assert declared[KT_TITLE]["slug"] == "sales.payment_methods.kuveytturk"


def test_kaynak_rozeti_bicim_farkina_aldanmaz() -> None:
    # Etkin değer "1" (metin), ilan edilen varsayılan 1 (sayı). Ham
    # karşılaştırma "veritabanından" derdi; ikisi aynı değer.
    assert config_map.source_of("1", 1) == "default"
    assert config_map.source_of(False, None) == "stored"
    assert config_map.source_of(None, None) == "unset"
    assert config_map.source_of(None, "kgs") == "default"
    assert config_map.source_of("Kargo", None) == "stored"


def test_mantiksal_alan_metin_olarak_yazilir() -> None:
    # `core_config` her şeyi metin tutuyor; `true` göndermek "true" METNİ
    # olarak saklanır ve (bool) dönüşümünde hep doğru çıkardı.
    assert config_map.to_store(False, "boolean") == "0"
    assert config_map.to_store("0", "boolean") == "0"
    assert config_map.to_store("", "boolean") == "0"
    assert config_map.to_store(True, "boolean") == "1"
    assert config_map.to_store("1", "boolean") == "1"


def test_beyaz_liste_sir_tasiyan_alan_icermez() -> None:
    # Beyaz listeye sonradan parola/görsel alanı eklenmesin diye kapı burada.
    declared = config_map.declared_fields(live_tree())
    for code in config_map.FLAT:
        field = declared.get(code)
        if field is not None:
            assert field["type"] not in config_map.SECRET_TYPES, code
    assert KT_PASSWORD not in config_map.FLAT


def test_kapanan_odeme_yontemi_adiyla_sayilir() -> None:
    declared = config_map.declared_fields(live_tree())
    assert config_map.going_dark({KT_ACTIVE: "0"}, declared) == \
        ["KuveytTürk sanal POS — açık"]
    assert config_map.going_dark({KT_ACTIVE: "1"}, declared) == []
    # Beyaz listede olsa da kritik olmayan alan bu listeye girmez.
    assert config_map.going_dark({KT_TITLE: ""}, declared) == []


# ============================================================ okuma — /config

async def test_yalniz_beyaz_liste_gosterilir() -> None:
    service, _, _ = _service()
    result = await service.config_view()
    codes = {field["code"] for group in result["groups"] for field in group["fields"]}
    assert KT_PASSWORD not in codes           # sır ekrana hiç gelmez
    assert "sales.payment_methods.kuveytturk.hash_check" not in codes
    assert KT_ACTIVE in codes


async def test_bulunmayan_anahtar_salt_okunur_ve_nedeni_yazili() -> None:
    service, api, _ = _service()
    # Mağaza kimliği düğümü hiç yokmuş gibi: canlıda `shop_information` da
    # tam olarak böyle YOK.
    api.menu_tree = [node for node in live_tree()[0]["children"]
                     if node["key"] != "sales.shipping.origin"]
    result = await service.config_view()
    field = field_of(result, STORE_NAME)
    assert field["found"] is False
    assert field["editable"] is False
    assert "core_config" in field["reason"]
    assert "Mağaza adı" in result["missing"]


async def test_sir_tasiyan_tip_beyaz_listeye_girse_bile_okunur_kalir() -> None:
    service, api, _ = _service()
    # Mağaza aynı kodu bir gün `password` tipiyle ilan ederse alan kapanmalı.
    for node in api.menu_tree[0]["children"]:
        for field in node["fields"]:
            if field["code"] == KT_TITLE:
                field["type"] = "password"
    result = await service.config_view()
    field = field_of(result, KT_TITLE)
    assert field["editable"] is False
    assert field["value"] == ""               # sır ekrana gitmez
    assert "password" in field["reason"]


async def test_kaynak_rozeti_alan_basina_doner() -> None:
    service, _, _ = _service()
    result = await service.config_view()
    assert field_of(result, GUEST)["source"] == "default"
    assert field_of(result, THRESHOLD)["source"] == "default"
    assert field_of(result, KT_TITLE)["source"] == "stored"
    assert field_of(result, STORE_NAME)["source"] == "unset"


async def test_varsayilana_don_icin_deger_gonderilir() -> None:
    service, _, _ = _service()
    result = await service.config_view()
    assert field_of(result, THRESHOLD)["hasDefault"] is True
    assert field_of(result, THRESHOLD)["default"] == "0"
    # Varsayılanı ilan edilmemiş alanda düğme gösterilmez.
    assert field_of(result, KT_TITLE)["hasDefault"] is False


async def test_ayar_agaci_kanal_KODUYLA_okunur_kimlikle_degil() -> None:
    # CANLIDA ÖLÇÜLDÜ (2026-08-13): `/configuration/menu?channel=1` ile
    # `channel=zzzyok` BİREBİR aynı yanıtı veriyor — 49 alan sessizce
    # varsayılana düşüyor, hata YOK. Kanal kimliği göndermek bu uçta olmayan
    # bir kanal göndermekle aynıdır ve saklanmış ayara "varsayılan" rozeti
    # taktırırdı. Sipariş ucu bunun TERSİNİ ister; oraya bakıp burayı
    # "düzeltmek" sessiz veri hatası üretir.
    service, api, _ = _service()
    await service.config_view()
    call = api.used("configuration_menu")[0]
    assert call["channel"] == "default"        # KOD
    assert not call["channel"].isdigit()       # kimlik değil
    assert call["locale"] == "tr"


async def test_yazma_okumayla_ayni_kanal_ve_dile_gider() -> None:
    # Okuma `default` kanalından yapılıp yazma başka kapsama giderse kullanıcı
    # ekranda görmediği bir değeri değiştirmiş olur.
    service, api, _ = _service()
    await service.save_config(changes={KT_TITLE: "Kart"}, reason=REASON, actor="Ali",
                              dry_run=False)
    read = api.used("configuration_menu")[0]
    write = api.used("update_configuration")[0]
    assert write["channel"] == read["channel"] == "default"
    assert write["locale"] == read["locale"] == "tr"


async def test_agac_tek_istekte_okunur() -> None:
    # On bir ayrı `configuration(slug)` çağrısı dakikada 55 isteklik kovayı
    # ayar ekranına harcardı.
    service, api, _ = _service()
    await service.config_view()
    assert len(api.used("configuration_menu")) == 1
    assert api.used("configuration_menu")[0]["include_values"] is True


async def test_magaza_dusunce_hicbir_alan_acilmaz() -> None:
    service, api, _ = _service()
    api.fail.add("configuration_menu")
    result = await service.config_view()
    assert result["ok"] is True               # uç patlamaz (K7)
    assert result["connected"] is False
    assert result["groups"] == []


async def test_bos_agac_baglanti_var_sayilmaz() -> None:
    # Boş ağaç "hiçbir anahtar yok" demektir; alanları düzenlenebilir açmak
    # kullanıcıyı var olmayan anahtarlara yazmaya davet ederdi.
    service, api, _ = _service()
    api.menu_tree = []
    result = await service.config_view()
    assert result["connected"] is False
    assert result["groups"] == []


async def test_bakim_modu_kanaldan_okunur_ve_salt_okunurdur() -> None:
    service, api, _ = _service()
    api.channel_rows = [{"id": 1, "code": "default", "isMaintenanceOn": True,
                         "maintenanceModeText": "Yakında", "allowedIps": "1.2.3.4"}]
    result = await service.config_view()
    info = result["maintenance"]
    assert info["available"] is True
    assert info["enabled"] is True
    assert info["message"] == "Yakında"
    assert "core_config" in info["note"]
    # Ekran bu bölümden yazma ucu ÇAĞIRMAZ: kanal güncellenmedi.
    assert api.used("update_channel") == []


# =========================================================== yazma — /config

async def test_gerekce_kisa_ise_hicbir_sey_yazilmaz() -> None:
    service, api, _ = _service()
    result = await service.save_config(changes={KT_TITLE: "Kart"}, reason="kısa", actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []


async def test_bulunmayan_anahtara_yazilmaz() -> None:
    service, api, _ = _service()
    api.menu_tree = [node for node in live_tree()[0]["children"]
                     if node["key"] != "sales.shipping.origin"]
    result = await service.save_config(changes={STORE_NAME: "BBD"}, reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []
    assert result["skipped"][0]["code"] == STORE_NAME


async def test_beyaz_liste_disi_kod_reddedilir() -> None:
    # Ekran gizlemek yetkilendirme değildir: gövdeye elle yazılan kod da
    # geçmemeli.
    service, api, _ = _service()
    result = await service.save_config(
        changes={"sales.payment_methods.kuveytturk.merchant_id": "1"},
        reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []


async def test_sir_tasiyan_alan_yazilmaz() -> None:
    service, api, _ = _service()
    result = await service.save_config(changes={KT_PASSWORD: "yeni"}, reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []


async def test_degisiklikler_slug_basina_tek_istekte_gider() -> None:
    service, api, _ = _service()
    result = await service.save_config(
        changes={KT_ACTIVE: "0", KT_TITLE: "Kart ile öde", THRESHOLD: "5"},
        reason=REASON, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    calls = api.used("update_configuration")
    assert len(calls) == 2                     # iki slug, iki istek
    slugs = [args[0] for args in api.args("update_configuration")]
    assert sorted(slugs) == ["catalog.inventory.stock_options",
                             "sales.payment_methods.kuveytturk"]
    payload = next(call["values"] for call in calls
                   if KT_ACTIVE in call["values"])
    assert payload[KT_ACTIVE] == "0"           # metin, mantıksal değil
    assert payload[KT_TITLE] == "Kart ile öde"


async def test_anahtar_kaydetme_aninda_yeniden_dogrulanir() -> None:
    # Ekran açıkken mağazada alan ilan edilmez hâle gelirse, açılıştaki
    # bilgiye güvenmek tam da kaçınılmak istenen sessiz satırı açardı.
    service, api, _ = _service()
    await service.config_view()
    api.menu_tree = []
    result = await service.save_config(changes={KT_TITLE: "Kart"}, reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []


async def test_kismi_yazma_gizlenmez() -> None:
    service, api, _ = _service()
    api.fail.add("update_configuration")
    result = await service.save_config(changes={KT_TITLE: "Kart"}, reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert result["written"] == []


async def test_yazma_denetim_izine_gerekcesiyle_gecer() -> None:
    service, _api, store = _service()
    await service.save_config(changes={KT_TITLE: "Kart"}, reason=REASON, actor="Ayşe",
                              dry_run=False)
    row = store.audit[-1]
    assert row["action"] == "save_config"
    assert row["reason"] == REASON
    assert row["actor"] == "Ayşe"
    assert row["result"] == "ok"


async def test_kuru_prova_gecide_bildirilir() -> None:
    service, api, _ = _service()
    result = await service.save_config(changes={KT_TITLE: "Kart"}, reason=REASON, actor="Ali",
                                       dry_run=True)
    assert result["dryRun"] is True
    assert api.used("update_configuration")[0]["dry_run"] is True


async def test_cok_fazla_alan_reddedilir() -> None:
    service, api, _ = _service()
    changes = {f"kod.{index}": "1" for index in range(config_map.MAX_CHANGES + 1)}
    result = await service.save_config(changes=changes, reason=REASON, actor="Ali")
    assert result["ok"] is False
    assert api.used("configuration_menu") == []   # ağaç bile okunmaz
