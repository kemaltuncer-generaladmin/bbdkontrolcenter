"""Cihaz, ayar, komut ve sipariş metotlarının sözleşmeye bağlı kuralları.

Buradaki denetimlerin ortak özelliği: hepsi isteği GÖNDERMEDEN keser.
Sebep tek — Laravel tanımadığı alanı sessizce yok sayar. "Kaydedildi" diyen
bir ekranın arkasında hiçbir yere yazılmamış bir değer bırakmak, açık bir
hatadan çok daha pahalıdır.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from bld_api_backend.client import COMMANDS, MANAGED_SETTINGS
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def reddeden(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError("istek gitmemeliydi")


# ------------------------------------------------------------------ ayarlar

def test_yonetilen_ayar_sayisi_yirmi_dorttur() -> None:
    # Sözleşme §2.2: mevcut 16 + kilit politikası 7. K-22 §1: + olay bazlı ses.
    assert len(MANAGED_SETTINGS) == 24
    assert len(set(MANAGED_SETTINGS)) == 24
    assert "disabled_sound_events" in MANAGED_SETTINGS


async def test_tanimsiz_ayar_anahtari_istek_gonderilmeden_reddedilir() -> None:
    api, _, _, _ = gateway(reddeden)
    with pytest.raises(BldApiError) as hata:
        await api.update_device_settings(3, settings={"poll_seconds": 5, "hizli_mod": True},
                                         reason="Yoklama süresi düşürüldü", actor="Ayşe Yılmaz")

    assert hata.value.code == "payload"
    assert "hizli_mod" in hata.value.message


async def test_kilit_alanindaki_null_govdede_korunur() -> None:
    """`null` = "yönetici dokunmadı" = SERBEST; `false` = kilitli. İkisi ayrı
    şeydir ve `None`'ı düşürmek bir kilidi kaldırmayı imkânsız kılardı."""
    ham_govdeler: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ham_govdeler.append(request.content)
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.update_device_settings(
        3,
        settings={"allow_settings": None, "allow_order_edit": False,
                  "lock_message": "Bu işlem merkezden kapatıldı."},
        reason="Kasa kilit politikası güncellendi", actor="Ayşe Yılmaz",
    )

    ayarlar = json.loads(ham_govdeler[0])["settings"]
    assert "allow_settings" in ayarlar
    assert ayarlar["allow_settings"] is None       # düşürülmedi
    assert ayarlar["allow_order_edit"] is False
    assert ayarlar["lock_message"] == "Bu işlem merkezden kapatıldı."
    # GÖNDERİLEN baytta gerçekten `null` var — imza bu baytların üzerinde.
    assert b'"allow_settings":null' in ham_govdeler[0]


async def test_olay_bazli_ses_ayari_bos_dizeyle_de_gonderilir() -> None:
    """`""` = "hiçbiri kapalı olmasın"; `null` = "yönetici dokunmadı".

    Boş dizeyi düşüren ya da `null`a çeviren bir geçit, kasadaki bütün
    susturmaları kaldırma komutunu "hiç dokunma"ya çevirirdi.
    """
    ham_govdeler: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ham_govdeler.append(request.content)
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.update_device_settings(
        3, settings={"disabled_sound_events": ""},
        reason="Bütün sesli uyarılar açılsın", actor="Ayşe Yılmaz")
    await api.update_device_settings(
        3, settings={"disabled_sound_events": "newOrder,bbdOrder"},
        reason="Gece vardiyasında sesler kısıldı", actor="Ayşe Yılmaz")

    assert json.loads(ham_govdeler[0])["settings"] == {"disabled_sound_events": ""}
    assert b'"disabled_sound_events":""' in ham_govdeler[0]
    assert json.loads(ham_govdeler[1])["settings"] == {
        "disabled_sound_events": "newOrder,bbdOrder"}


async def test_bos_ayar_sozlugu_reddedilir() -> None:
    api, _, _, _ = gateway(reddeden)
    with pytest.raises(BldApiError) as hata:
        await api.update_device_settings(3, settings={}, reason="Boş güncelleme denemesi",
                                         actor="Ayşe Yılmaz")

    assert hata.value.code == "payload"


# ------------------------------------------------------------------ komut

def test_komut_listesi_sekizdir() -> None:
    # Sözleşme §2.3'ün beşi + K-22 §2'nin üçü (`KitchenCommand::ALL`).
    assert COMMANDS == ("test_receipt", "reprint", "clear_failed", "silence_alarm",
                        "restart", "update", "unpair", "clear_queue")


async def test_yeni_komutlar_yuksuz_gider() -> None:
    # `update` · `unpair` · `clear_queue` yük İSTEMEZ. Yük verilmediğinde
    # gövdeye `"payload"` anahtarı HİÇ konmamalı: sözleşmede olmayan bir alan
    # taşımak, Laravel'in onu sessizce yok saymasına güvenmek olurdu.
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return json_response({"id": 7})

    api, _, _, _ = gateway(handler)
    for komut in ("update", "unpair", "clear_queue"):
        await api.send_command(3, command=komut, reason="Kasa sürümü çok eski",
                               actor="Ayşe Yılmaz")

    assert [govde["command"] for govde in govdeler] == ["update", "unpair", "clear_queue"]
    assert all("payload" not in govde for govde in govdeler)


async def test_tanimsiz_komut_reddedilir() -> None:
    api, _, _, _ = gateway(reddeden)
    with pytest.raises(BldApiError) as hata:
        await api.send_command(3, command="reboot_now", reason="Kasa yanıt vermiyor",
                               actor="Ayşe Yılmaz")

    assert hata.value.code == "payload"
    assert "reboot_now" in hata.value.message


async def test_reprint_fis_turu_zorunludur() -> None:
    api, _, _, _ = gateway(reddeden)
    with pytest.raises(BldApiError) as hata:
        await api.send_command(3, command="reprint", payload={"order_id": 12},
                               reason="Fiş yazıcıda sıkıştı", actor="Ayşe Yılmaz")

    assert hata.value.code == "payload"
    assert "mutfak" in hata.value.message


async def test_gecerli_komut_yukuyle_birlikte_gider() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return json_response({"id": 5, "command": "reprint"})

    api, depo, _, _ = gateway(handler)
    await api.send_command(3, command="reprint", payload={"order_id": 12, "type": "mutfak"},
                           reason="Fiş yazıcıda sıkıştı", actor="Ayşe Yılmaz")

    assert govdeler[0]["command"] == "reprint"
    assert govdeler[0]["payload"] == {"order_id": 12, "type": "mutfak"}
    assert depo.audit[0]["action"] == "command:reprint"


# --------------------------------------------------------------- sipariş

async def test_bos_revizyon_listesi_reddedilir() -> None:
    """Gönderilen liste siparişin TAM hâlidir; boş liste "hepsini sil"
    anlamına gelirdi ve iptal işi durum ucunun işidir."""
    api, _, _, _ = gateway(reddeden)
    with pytest.raises(BldApiError) as hata:
        await api.create_order_revision(12, items=[], reason="Müşteri kalem çıkardı",
                                        actor="Ayşe Yılmaz")

    assert hata.value.code == "payload"


async def test_revizyon_govdesi_tam_listeyi_ve_istege_bagli_alanlari_tasir() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return json_response({"id": 4})

    api, _, _, _ = gateway(handler)
    await api.create_order_revision(
        12, items=[{"menu_id": 7, "quantity": 2}], reason="Müşteri iki porsiyon istedi",
        actor="Ayşe Yılmaz", note="Acele",
    )

    govde = govdeler[0]
    assert govde["items"] == [{"menu_id": 7, "quantity": 2}]
    assert govde["note"] == "Acele"
    # Verilmeyen isteğe bağlı alanlar gövdeye HİÇ konmaz: sunucu "verilmedi"
    # ile "boş verildi" arasındaki farkı ancak böyle görebilir.
    assert "requested_at" not in govde
    assert "customer_note" not in govde


async def test_siparis_suzgecleri_sorgu_dizesine_gider() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"data": []})

    api, _, _, _ = gateway(handler)
    await api.orders(include_completed=True, since="2026-08-14T09:00:00Z")

    assert istekler[0].url.params["include_completed"] == "true"
    assert istekler[0].url.params["since"] == "2026-08-14T09:00:00Z"


async def test_fis_kuyrugu_denetim_kaydidir_ve_suzgecsiz_de_calisir() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"data": [{"id": 1, "order_id": 12, "type": "mutfak"}]})

    api, _, _, _ = gateway(handler)
    satirlar = await api.print_jobs()

    assert satirlar == [{"id": 1, "order_id": 12, "type": "mutfak"}]
    assert istekler[0].url.query == b""


# ----------------------------------------------------------------- durum

def test_state_snake_case_dondurur() -> None:
    api, _, _, _ = gateway(reddeden, read_only=True, dry_run_default=True)
    durum = api.state()

    # BLD sözleşmesinin tamamı snake_case (§2); aynı ekranda iki adlandırma
    # bulundurmak yazım hatasını sessiz bırakırdı.
    assert durum == {
        "base_url": "https://ornek.test",
        "read_only": True,
        "dry_run_default": True,
        "require_reason": True,
    }


async def test_saglik_yoklamasi_ozet_ucunu_kullanir() -> None:
    yollar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        yollar.append(request.url.path)
        return json_response({"devices": {"total": 2}, "server_time": "2026-08-14T09:00:00Z"})

    api, _, _, _ = gateway(handler)
    sonuc = await api.health()

    assert sonuc["ok"] is True
    assert yollar == ["/api/control/kds/overview"]


async def test_saglik_yoklamasi_hatayi_yutar_ve_kodu_soyler() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="404 Not Found")

    api, _, _, _ = gateway(handler)
    sonuc = await api.health()

    # Ekran ayakta kalır (K7) ama durumu SÖYLER.
    assert sonuc["ok"] is False
    assert sonuc["code"] == "control_endpoint_missing"
