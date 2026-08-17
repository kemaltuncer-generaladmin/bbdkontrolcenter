"""Doğrulama hatası (422) da tekdüze zarfa girer ve Türkçe konuşur.

BURASI EKSİKTİ VE HER MODÜLÜ VURUYORDU. Kabuk hata metnini
`payload?.error?.message || payload?.detail` sırasıyla okur (ui-kernel.js).
`HTTPException` zarfa giriyordu; doğrulama hatası ise FastAPI'nin varsayılan
`{"detail": [{...}, {...}]}` DİZİSİ olarak çıkıyordu. Dizi `error.message`e
düşünce ekranda **`[object Object]`** yazıyordu — yani doldurulmamış bir
alanın karşılığı, kullanıcının okuyamadığı bir hataydı.

Somut belirtisi: Kargo Yönetimi'nde gerekçe kutusu "boş bırakılabilir" diyor,
kullanıcı boş bırakıyor, uç `min_length=10` istiyor ve ekranda
`[object Object]` beliriyordu. Şema o uçta gevşetildi ama BİÇİM sorunu
ayrıydı ve tek başına kalsaydı bir sonraki zorunlu alanda aynen tekrarlardı.

Ağa çıkmaz, çekirdeğin kendi uygulamasını kurar. DEPO GEÇİCİ DİZİNDEDİR:
ayarsız `create_app()` geliştiricinin gerçek `data/kontrol-merkezi.sqlite`
dosyasını açar ve çıktı kaydı da (ADR 0019) o depoya bağlanırdı. Biçim sınayan
bir testin gerçek depoya dokunması için hiçbir neden yok.
"""

from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app


def _uygulama(tmp_path: Path) -> FastAPI:
    """Çekirdek uygulaması — deposu, kasası ve günlüğü geçici dizinde."""
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["files"] = {"output_path": str(tmp_path / "cikti")}
    return create_app(Config(data, root=ROOT))


class Gövde(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    packages: int = Field(ge=1, le=99)


def test_422_govdesi_DIZI_DEGIL_tekduze_zarf(tmp_path: Path) -> None:
    """`detail` dizisi değil, `error.message` metni dönmeli.

    Kabuk `error.message` okuyabildiği sürece kullanıcı gerçek cümleyi görür;
    dizi döndüğü an `[object Object]` yazardı.
    """
    app: FastAPI = _uygulama(tmp_path)

    @app.post("/sinama/dogrulama")
    async def _sinama(body: Gövde) -> dict[str, str]:  # pragma: no cover
        return {"ok": "evet"}

    with TestClient(app, raise_server_exceptions=False) as client:
        cevap = client.post("/sinama/dogrulama", json={})

    assert cevap.status_code == 422
    govde = cevap.json()
    assert "error" in govde, govde
    assert isinstance(govde["error"]["message"], str)
    assert govde["error"]["status"] == 422
    # `detail` ANAHTARI HİÇ OLMAMALI: kabuk onu yedek olarak okuyor ve dizi
    # gelirse yine `[object Object]` basardı.
    assert "detail" not in govde


def test_eksik_alan_TURKCE_ve_alan_adiyla_soyleniyor(tmp_path: Path) -> None:
    app: FastAPI = _uygulama(tmp_path)

    @app.post("/sinama/dogrulama")
    async def _sinama(body: Gövde) -> dict[str, str]:  # pragma: no cover
        return {"ok": "evet"}

    with TestClient(app, raise_server_exceptions=False) as client:
        mesaj = client.post("/sinama/dogrulama", json={}).json()["error"]["message"]

    # Kullanıcı "field required" değil, kendi dilinde ve KUTUNUN ADIYLA görmeli.
    assert "reason" in mesaj
    assert "packages" in mesaj
    assert "zorunlu" in mesaj
    assert "field required" not in mesaj.lower()


def test_kisa_metin_SINIRI_SAYIYLA_soyluyor(tmp_path: Path) -> None:
    app: FastAPI = _uygulama(tmp_path)

    @app.post("/sinama/dogrulama")
    async def _sinama(body: Gövde) -> dict[str, str]:  # pragma: no cover
        return {"ok": "evet"}

    with TestClient(app, raise_server_exceptions=False) as client:
        mesaj = client.post("/sinama/dogrulama",
                            json={"reason": "kısa", "packages": 1}).json()["error"]["message"]

    # "en az 10 karakter" demek, kullanıcının ne yapacağını söyler;
    # "string_too_short" söylemez.
    assert "en az 10 karakter" in mesaj
    assert "string_too_short" not in mesaj


def test_sayi_siniri_da_okunur_yazilir(tmp_path: Path) -> None:
    app: FastAPI = _uygulama(tmp_path)

    @app.post("/sinama/dogrulama")
    async def _sinama(body: Gövde) -> dict[str, str]:  # pragma: no cover
        return {"ok": "evet"}

    with TestClient(app, raise_server_exceptions=False) as client:
        mesaj = client.post("/sinama/dogrulama",
                            json={"reason": "yeterince uzun gerekçe",
                                  "packages": 0}).json()["error"]["message"]

    assert "packages" in mesaj
    assert "1" in mesaj


def test_TANIMADIGIMIZ_hata_turu_yutulmaz(tmp_path: Path) -> None:
    """Bilinmeyen doğrulama türü listeden DÜŞMEZ, kendi mesajıyla gösterilir.

    Tanımadığımız bir hatayı atlamak, ekranı "sorun yok" der hâle getirirdi:
    istek reddedilmiş ama kullanıcı nedenini hiç görmemiş olurdu.
    """
    app: FastAPI = _uygulama(tmp_path)

    @app.get("/sinama/patlat")
    async def _patlat() -> dict[str, str]:  # pragma: no cover
        raise RequestValidationError([
            {"type": "bilinmeyen_tur", "loc": ("body", "tuhaf"),
             "msg": "beklenmedik bir şey", "input": None},
        ])

    with TestClient(app, raise_server_exceptions=False) as client:
        cevap = client.get("/sinama/patlat")

    assert cevap.status_code == 422
    mesaj = cevap.json()["error"]["message"]
    assert "tuhaf" in mesaj
    assert "beklenmedik bir şey" in mesaj
