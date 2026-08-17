"""Gerekçe politikasının KAPSAM testi — hangi yol gerekçe ister, hangisi istemez.

NEDEN BU TEST VAR — KORUDUĞU İKİ ARIZA

    1. GEVŞEK TARAF. Gerekçe artık küresel değil, uç başına isteniyor
       (`_REASON_OPTIONAL` defteri). Bir muafiyetin yanlış yola yazılması —
       diyelim `POST publish` deseninin kazara defterde bitmesi — yayına alma
       gibi geri alınması zor bir işlemi sessizce gerekçesiz bırakırdı ve
       hiçbir ekran bunu söylemezdi: yazma çalışır, yalnız denetim izindeki
       "neden" sütunu boş kalır.

    2. SIKI TARAF. Muafiyeti unutmak da bir arızadır ama GÜRÜLTÜLÜDÜR: kalem
       eklemek `reason_required` ile geri döner ve panel kullanıcıya hatayı
       gösterir. Varsayılan bu yüzden "gerekçe İSTER" tarafındadır ve
       aşağıdaki tablo bunu yol yol sabitliyor.

TABLO ELLE YAZILDI. `_REASON_OPTIONAL` defterini koddan okuyup kendine karşı
doğrulamak hiçbir şey kanıtlamazdı; buradaki satırlar görev metnindeki karar
tablosunun kopyasıdır ve defter ondan ayrıştığında test düşer.

Hiçbir test ağa çıkmaz: `httpx.MockTransport`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from bld_api_backend.client import MENU, ORDERS, BldApi
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway

GEREKCE = "Tavuk tedariki azaldı, perşembe menüsü değişti"
AKTOR = "Ayşe Yılmaz"

Cagri = Callable[[BldApi], Coroutine[Any, Any, Any]]

#: `control/menu` alanının bütün yazmaları: (ad, gerekçesiz çağrı, gerekçe ister mi).
#:
#: ÖLÇÜT: işlem müşteriye GÖRÜNÜR HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu.
#: Taslak kurmak ikisi de değil; yayınlamak, yayından çekmek, günü silmek ve
#: kopyalamak ikisi de.
#:
#: GEREKÇE İSTEYEN METOTLARDA `reason=""` AÇIKÇA YAZILIR: imzada varsayılanı
#: YOK ve olmaması bilinçli — o uçlara gerekçe unutularak değil, ancak bile
#: bile boş geçilebilir.
MENU_YAZMALARI: tuple[tuple[str, Cagri, bool], ...] = (
    ("create_menu_day",
     lambda api: api.create_menu_day(date="2026-08-17", actor=AKTOR), False),
    ("update_menu_day",
     lambda api: api.update_menu_day("2026-08-17", package_price_kurus=19000,
                                     actor=AKTOR), False),
    ("create_menu_item",
     lambda api: api.create_menu_item("2026-08-17", menu_id=27, actor=AKTOR), False),
    ("update_menu_item",
     lambda api: api.update_menu_item("2026-08-17", 902, quantity=2, actor=AKTOR), False),
    ("delete_menu_item",
     lambda api: api.delete_menu_item("2026-08-17", 902, actor=AKTOR), False),
    ("set_menu_stock",
     lambda api: api.set_menu_stock("2026-08-17", capacity_total=120,
                                    items=[{"item_id": 902, "capacity": 60}],
                                    actor=AKTOR), False),

    ("delete_menu_day",
     lambda api: api.delete_menu_day("2026-08-17", reason="", actor=AKTOR), True),
    ("publish_menu_day",
     lambda api: api.publish_menu_day("2026-08-17", reason="", actor=AKTOR), True),
    ("unpublish_menu_day",
     lambda api: api.unpublish_menu_day("2026-08-17", reason="", actor=AKTOR), True),
    ("duplicate_menu_day",
     lambda api: api.duplicate_menu_day("2026-08-17", target_date="2026-08-24",
                                        reason="", actor=AKTOR), True),
)


def _kabul(gonderilen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return httpx.Response(200, json={"ok": True, "dry_run": False, "data": {}})

    return handler


@pytest.mark.parametrize(("ad", "cagri", "ister"), MENU_YAZMALARI,
                         ids=[name for name, _, _ in MENU_YAZMALARI])
async def test_menu_ucu_gerekce_politikasina_uyar(ad: str, cagri: Cagri,
                                                  ister: bool) -> None:
    """Gerekçesiz çağrı: gerekçe isteyen uçta yerelde durur, istemeyende geçer."""
    gonderilen: list[httpx.Request] = []
    api, _, _, _ = gateway(_kabul(gonderilen))

    if ister:
        with pytest.raises(BldApiError) as hata:
            await cagri(api)
        assert hata.value.code == "reason_required", f"{ad}: yanlış hata kodu"
        # İSTEK HİÇ ÇIKMADI: hız kovasından pay harcanmaz ve sunucu 422
        # üretmek zorunda kalmaz.
        assert gonderilen == [], f"{ad}: gerekçesiz istek sunucuya gitti"
        return

    await cagri(api)
    assert len(gonderilen) == 1, f"{ad}: istek gönderilmedi"
    govde = json.loads(gonderilen[0].content)
    # ALAN HİÇ BULUNMAZ. `reason: ""` göndermek "gerekçe verildi ve boştu"
    # der; alanı hiç göndermemek "bu uçta gerekçe sorulmadı" der ve sunucu
    # ikincisini bekliyor (`sometimes|nullable`).
    assert "reason" not in govde, f"{ad}: gövdede boş gerekçe alanı var"
    # AKTÖR HER KOŞULDA: seyrekleşen soru "neden", "kim" değil.
    assert govde["actor"] == AKTOR, f"{ad}: aktör gövdeye konmamış"


async def test_muaf_ucta_elle_verilen_gerekce_korunur() -> None:
    """Muaf uca yine de gerekçe geçilirse sessizce düşürülmez.

    Sunucu bu uçlarda gerekçeyi `sometimes|nullable` olarak kabul ediyor.
    Yazılmış bir notu tele koymamak, kullanıcının yazdığı bilgiyi yok etmek
    olurdu — üstelik hiçbir yerde söylenmeden.
    """
    gonderilen: list[httpx.Request] = []
    api, _, _, _ = gateway(_kabul(gonderilen))

    await api.create_menu_item("2026-08-17", menu_id=27, reason=GEREKCE, actor=AKTOR)

    assert json.loads(gonderilen[0].content)["reason"] == GEREKCE


async def test_muaf_ucta_kisa_gerekce_istegi_durdurmaz() -> None:
    """ALT SINIR MUAF UÇTA UYGULANMAZ.

    İsteğe bağlı bir notu "çok kısa" diye 422 ile geri çevirmek, kalem
    eklemeyi durdururdu — hızlanmak için yapılan değişikliğin tersi.
    """
    gonderilen: list[httpx.Request] = []
    api, _, _, _ = gateway(_kabul(gonderilen))

    await api.create_menu_item("2026-08-17", menu_id=27, reason="not", actor=AKTOR)

    assert json.loads(gonderilen[0].content)["reason"] == "not"


async def test_muaf_ucta_uzun_gerekce_yine_reddedilir() -> None:
    """ÜST SINIR MUAF UÇTA DA GEÇERLİ: kolon genişliği politikayla değişmedi."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_menu_item("2026-08-17", menu_id=27, reason="x" * 501,
                                   actor=AKTOR)

    assert hata.value.code == "reason_required"


async def test_muaf_ucta_aktor_hala_zorunlu() -> None:
    """Muafiyet defteri AKTÖRÜ KAPSAMAZ.

    Gerekçesiz bir kalem eklemesi bile kimin yaptığını kayda geçirir; denetim
    izinde kasadan mı merkezden mi yapıldığı ancak böyle ayrılıyor.
    """
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_menu_item("2026-08-17", menu_id=27, actor="  ")

    assert hata.value.code == "actor_required"


async def test_muaf_ucta_yerel_denetim_satiri_yine_acilir() -> None:
    """SATIR GEREKÇEDEN BAĞIMSIZDIR.

    Ağ koparsa "kim neyi denedi" sorusunun cevabı yalnız bu satırda kalır.
    Gerekçesiz yazmada satırı hiç açmamak, hızlanma uğruna izin kendisini
    atmak olurdu; atılan tek şey `reason` sütununun DOLULUĞUDUR.
    """
    gonderilen: list[httpx.Request] = []
    api, depo, _, _ = gateway(_kabul(gonderilen))

    await api.create_menu_item("2026-08-17", menu_id=27, actor=AKTOR)

    assert len(depo.audit) == 1
    assert depo.audit[0]["reason"] == ""
    assert depo.audit[0]["actor"] == AKTOR
    assert depo.audit[0]["action"] == "menu.item.create"
    assert depo.audit[0]["result"] == "ok"


async def test_defterde_olmayan_menu_yolu_gerekce_ister() -> None:
    """VARSAYILAN GÜVENLİ TARAFTA.

    Yarın `control/menu` altına bir uç eklenip muafiyeti YAZILMAZSA gerekçe
    istenir. Tersi bir defter ("gerekçe isteyenler") kurulsaydı, aynı unutkanlık
    o ucu sessizce gerekçesiz bırakırdı.
    """
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api._request("POST", f"{MENU}/days/2026-08-17/freeze", body={},
                           actor=AKTOR, action="menu.freeze")

    assert hata.value.code == "reason_required"


async def test_ayni_yolun_iki_fiili_ayri_politikadadir() -> None:
    """`PATCH days/{date}` muaf, `DELETE days/{date}` DEĞİL.

    Defter yalnız yola baksaydı ikisi aynı kovaya düşer ve gün silme
    (kalemleriyle birlikte, geri alınamaz) sessizce gerekçesiz kalırdı.
    """
    yol = f"{MENU}/days/2026-08-17"
    assert BldApi._reason_optional("PATCH", yol) is True
    assert BldApi._reason_optional("DELETE", yol) is False


@pytest.mark.parametrize("cagri", [
    lambda api: api.revoke_device(3, reason="", actor=AKTOR),
    lambda api: api.update_product(27, price_kurus=10000, reason="", actor=AKTOR),
    lambda api: api.pause_ordering(reason="", actor=AKTOR),
    lambda api: api.cancel_order(8421, reason="", actor=AKTOR),
], ids=["kds", "products", "settings", "orders"])
async def test_muafiyet_defteri_alanlara_yayilmadi(cagri: Cagri) -> None:
    """MUAFİYET ADI ADINA VERİLDİ, ALANA DEĞİL.

    Defterde bugün yedi satır var: `control/menu`'nün altısı ve `control/orders`
    alanından YALNIZ `POST /orders`. Muafiyet bir alanın tamamına verilseydi,
    aşağıdaki `cancel_order` da sessizce gerekçesiz geçerdi — oysa iptal
    müşteriye görünür ve geri alınması zor bir işlemdir.
    """
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await cagri(api)

    assert hata.value.code == "reason_required"


# ------------------------------------------- elle sipariş (`POST /orders`)
#
# Sunucu bu uçta `reasonRequired: false` diyor. Defter ondan ayrı kalsaydı
# geçit, sunucunun kabul ettiği bir çağrıyı YERELDE keserdi: personel telefonda
# konuşurken on karakterlik bir gerekçe yazmak zorunda kalır, sınırın kaçındığı
# metinler ("sipariş", "asdasd") üretilirdi.

def _siparis(api: BldApi, **ek: Any) -> Any:
    return api.create_order(service_date="2026-08-18", delivery_type="pickup",
                            payment_method="cash", customer_id=312,
                            items=[{"menu_id": 88, "quantity": 12}], **ek)


async def test_elle_siparis_gerekcesiz_gecer() -> None:
    """Gerekçesiz çağrı yerelde DURMAZ ve gövdede boş bir `reason` alanı olmaz."""
    gonderilen: list[httpx.Request] = []
    api, _, _, _ = gateway(_kabul(gonderilen))

    await _siparis(api, actor=AKTOR)

    assert len(gonderilen) == 1, "gerekçesiz sipariş yerelde durduruldu"
    govde = json.loads(gonderilen[0].content)
    assert "reason" not in govde
    # GEREKÇE SEYRELDİ, İZ SEYRELMEDİ.
    assert govde["actor"] == AKTOR


async def test_elle_siparis_gerekcesiz_de_olsa_denetim_satiri_acar() -> None:
    """Ağ koparsa "kim hangi siparişi denedi" sorusunun cevabı yalnız burada."""
    gonderilen: list[httpx.Request] = []
    api, depo, _, _ = gateway(_kabul(gonderilen))

    await _siparis(api, actor=AKTOR)

    assert len(depo.audit) == 1
    assert depo.audit[0]["action"] == "order.create"
    assert depo.audit[0]["actor"] == AKTOR
    assert depo.audit[0]["reason"] == ""


async def test_elle_siparis_aktorsuz_gecmez() -> None:
    """Muafiyet defteri AKTÖRÜ KAPSAMAZ: seyrekleşen soru "neden", "kim" değil."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await _siparis(api, actor="  ")

    assert hata.value.code == "actor_required"


async def test_elle_sipariste_uzun_gerekce_yine_reddedilir() -> None:
    """ÜST SINIR MUAF UÇTA DA GEÇERLİ: sunucu 500 karakteri aşanı 422 ile döner."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await _siparis(api, reason="x" * 501, actor=AKTOR)

    assert hata.value.code == "reason_required"


def test_siparis_alaninda_yalniz_acma_muaf() -> None:
    """Aynı alanın dört yazması, iki ayrı politika.

    Sipariş AÇMAK rutin bir kayıt akışıdır; revizyon, durum geçişi ve iptal
    müşteriye görünür ve geri alınması zordur. Defter yalnız alana baksaydı
    ikisi aynı kovaya düşerdi.
    """
    assert BldApi._reason_optional("POST", ORDERS) is True
    for yol in (f"{ORDERS}/8421/revisions", f"{ORDERS}/8421/status",
                f"{ORDERS}/8421/cancel"):
        assert BldApi._reason_optional("POST", yol) is False


async def test_kuresel_salter_kapaliyken_hicbir_ucta_gerekce_aranmaz() -> None:
    """`require_reason: false` ayarı defterin ÜSTÜNDEDİR.

    Ayar bir acil kaçış yoludur ve kapsamı değişmedi: kapatıldığında yayınlama
    da gerekçesiz geçer. Politikanın kendisi defterdedir, ayarda değil.
    """
    gonderilen: list[httpx.Request] = []
    api, _, _, _ = gateway(_kabul(gonderilen), require_reason=False)

    await api.publish_menu_day("2026-08-17", reason="", actor=AKTOR)

    assert len(gonderilen) == 1
