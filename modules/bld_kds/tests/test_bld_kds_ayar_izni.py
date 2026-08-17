"""`bld_kds.settings` — kasa ayarı yazmanın ayrı izni (17.08.2026 kararı).

SINANAN SÖZ TEK CÜMLE: `bld_kds.manage` taşıyan ama `bld_kds.settings`
taşımayan bir oturum SİPARİŞ DURUMUNU DEĞİŞTİREBİLİR, KASA AYARINI YAZAMAZ.

Bu dosya diğer üçünden farklı olarak GERÇEK BİR HTTP KATMANI kaldırır.
Nedeni şu: `test_bld_kds_routes.py` her ucun `requires(...)` demetini okuyup
sözleşmeye karşı doğruluyor — yani "kapı DOĞRU ANAHTARI istiyor mu" sorusunu
cevaplıyor. Cevaplamadığı soru "kapı GERÇEKTEN KAPANIYOR mu": bir demeti
okumak, o demetin isteği durdurduğunu göstermez. İki izin arasındaki ayrımın
bütün değeri de tam olarak orada — anahtarı bölmek, isteği durdurmuyorsa
kâğıt üstünde kalır (K9).

KİMLİK TAKLİT EDİLİR, İZİN DENETİMİ EDİLMEZ. `requires` çekirdeğin gerçek
kodudur ve olduğu gibi koşar; taklit edilen yalnız "bu belirteç kime ait"
sorusudur (`resolve_session`). İzin kararını `CurrentUser.has_permission`
verir — o da çekirdeğin gerçek kodu. Taklit bir izin nesnesi yazsaydık test
kendi taklidini sınardı.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from bld_kds_backend.api import routes
from bld_kds_backend.service import KdsService
from bld_kds_fakes import DEVICE, ORDER, FakeApi, FakeBus, FakeLog, FakeStore
from fastapi import FastAPI
from fastapi.testclient import TestClient

from km_core.security.identity import CurrentUser

GEREKCE = "Kasa yoklama aralığı çok sık, oran sınırına giriyor"

#: Yazılacak ayar. `DEVICE` bugün 5 saniyede yokluyor; DEĞİŞEN bir değer
#: seçmek zorunlu — servis aynı değeri yeniden yazmaz ve geçide hiç çağrı
#: çıkmadan `ok: True` döner (`settings_updated_at` damgası bozulmasın diye).
AYAR = {"poll_seconds": 9}

#: Sipariş `hazirlaniyor` durumunda (bkz. `ORDER`); matriste sonraki adım bu.
SONRAKI_DURUM = "hazir"

#: İki oturum. Aradaki TEK fark `bld_kds.settings`; bir başka anahtarı da
#: değiştirseydik, düşen testin hangi anahtar yüzünden düştüğü belirsiz kalırdı.
PERSONEL = {"bld_kds.view", "bld_kds.manage"}
YONETICI = {"bld_kds.view", "bld_kds.manage", "bld_kds.settings"}


@dataclass
class SahteKimlik:
    """`app.state.identity` yüzeyinin denetim kapısının kullandığı kadarı."""

    oturumlar: dict[str, set[str]]
    reddedilenler: list[tuple[str, str]]

    async def resolve_session(self, token: str) -> CurrentUser | None:
        izinler = self.oturumlar.get(token)
        if izinler is None:
            return None
        return CurrentUser(
            id=f"u-{token}", first_name="Deniz", last_name="Kaya",
            org_scope="bld", roles=[], permissions=set(izinler),
        )

    async def audit(self, user_id: str, action: str, *, result: str = "ok",
                    detail: str = "", **_: Any) -> None:
        # Reddedilen istek denetim izine düşer; kapının sessizce kapanmadığını
        # bu liste gösterir.
        if result == "denied":
            self.reddedilenler.append((user_id, detail))


@pytest.fixture
def kurulum() -> tuple[TestClient, FakeApi, SahteKimlik]:
    api = FakeApi(devices=[dict(DEVICE)], orders={12: dict(ORDER)})
    service = KdsService(api=api, store=FakeStore(), log=FakeLog(), config={},
                         publish=FakeBus())

    app = FastAPI()
    app.include_router(routes.bind(service), prefix="/api/bld_kds")
    kimlik = SahteKimlik(oturumlar={"personel": PERSONEL, "yonetici": YONETICI},
                         reddedilenler=[])
    app.state.identity = kimlik
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, api, kimlik


def basliklar(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ayar_yaz(client: TestClient, token: str) -> Any:
    return client.patch(
        "/api/bld_kds/devices/3/settings",
        headers=basliklar(token),
        json={"reason": GEREKCE, "settings": dict(AYAR)},
    )


def _durum_yaz(client: TestClient, token: str) -> Any:
    return client.post(
        "/api/bld_kds/orders/12/status",
        headers=basliklar(token),
        json={"reason": GEREKCE, "status": SONRAKI_DURUM},
    )


# ----------------------------------------------------- kararın iki yarısı


def test_manage_tasiyan_personel_siparis_durumunu_degistirebilir(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """Kararın VERDİĞİ yarı. Daraltma bunu almasaydı iş yapılamazdı."""
    client, api, _ = kurulum

    cevap = _durum_yaz(client, "personel")
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["ok"] is True
    # İstek gerçekten BLD'ye gitti: 200 tek başına yetmez, servis `ok: False`
    # ile de 200 döner (HTTP hatası fırlatmaz).
    assert api.used("set_order_status") == [
        {"status": SONRAKI_DURUM, "reason": GEREKCE, "actor": "Deniz Kaya",
         "dry_run": False}
    ]


def test_settings_tasimayan_personel_kasa_ayarini_yazamaz(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """Kararın ALDIĞI yarı — tek istisna budur."""
    client, api, kimlik = kurulum

    cevap = _ayar_yaz(client, "personel")
    assert cevap.status_code == 403, cevap.text

    # KAPI GÖVDEYE GİRMEDEN KAPANIR: geçide tek bir çağrı bile çıkmamalı.
    # Servise girip orada reddedilseydi, `bld.api`ye taze okuma isteği gider ve
    # yetkisiz istek uzak sunucuda iz bırakırdı.
    assert api.calls == []
    # Sessizce kapanmaz: reddedilen istek denetim izine düşer.
    assert kimlik.reddedilenler == [("u-personel", "bld_kds.settings")]


def test_ayni_oturum_ayni_cihaza_ADINI_yazabilir(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """Kapanan şey CİHAZ değil, AYAR.

    `PATCH /devices/{id}` ile `PATCH /devices/{id}/settings` aynı cihaza bakar
    ve yolları tek bir ek parçayla ayrılır. Bölünen anahtarın yanlışlıkla
    komşu ucu da kapatmadığı ancak burada görülür.
    """
    client, api, _ = kurulum

    cevap = client.patch(
        "/api/bld_kds/devices/3",
        headers=basliklar("personel"),
        json={"reason": GEREKCE, "name": "Mutfak Kasası 2"},
    )
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["ok"] is True
    assert api.used("rename_device")


def test_settings_tasiyan_yonetici_ayni_ayari_yazabilir(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """Kapı KAPALI değil, DAR: doğru anahtarla açılıyor.

    Bu olmadan yukarıdaki 403 bir şey kanıtlamazdı — gövdenin başka bir nedenle
    (şema, gerekçe, cihaz kimliği) reddedilmiş olması da aynı testi geçerdi.
    """
    client, api, _ = kurulum

    cevap = _ayar_yaz(client, "yonetici")
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["ok"] is True
    assert api.used("update_device_settings") == [
        {"settings": AYAR, "reason": GEREKCE,
         "actor": "Deniz Kaya", "dry_run": False}
    ]


# ------------------------------------------------- K9'un arayüz yarısının verisi


def test_okuma_ucu_ayar_yetkisini_bildirir(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """Panel ayar formunu salt okunur çizebilsin diye.

    Kabuk panele izin listesi VERMİYOR (`ui-kernel.js` → `mountPanel`); panelin
    izni sorabileceği tek yer bu yanıt. Yetkilendirme DEĞİLDİR — yukarıdaki
    403 kapıdır — ama bu bayrak olmasaydı panel, yetkisiz kullanıcıya 24 alanı
    doldurtup sonunda 403 gösterirdi.
    """
    client, _, _ = kurulum

    dar = client.get("/api/bld_kds/devices", headers=basliklar("personel"))
    genis = client.get("/api/bld_kds/devices", headers=basliklar("yonetici"))
    assert dar.status_code == 200 and genis.status_code == 200

    assert dar.json()["can"] == {"settings": False}
    assert genis.json()["can"] == {"settings": True}

    # Okuma UCU DARALMADI: ayar yazamayan da cihazı ve ayarlarının bugünkü
    # değerini görür. "Yoklama kaç saniyede" sorusu ayar yazmadan da sorulur.
    assert dar.json()["items"] == genis.json()["items"]
    assert len(dar.json()["settings_spec"]) == 24


def test_oturumsuz_istek_401_verir(
        kurulum: tuple[TestClient, FakeApi, SahteKimlik]) -> None:
    """403 ile 401 ayrı şeylerdir: biri "yetkin yok", öteki "kim olduğun
    bilinmiyor". İkisi karışırsa yetkisiz erişim, süresi dolmuş oturum gibi
    görünür ve kimse bakmaz."""
    client, _, _ = kurulum

    assert _ayar_yaz(client, "yok-boyle-bir-oturum").status_code == 401
