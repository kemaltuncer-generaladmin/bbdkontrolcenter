"""«KARGOYA VER» — tek tık, takip kodu otomatik, etiket + fatura basılır.

KULLANICININ KARARI, AYNEN: "sipariş seçince 'kargoya ver' dedik mi o sipariş
yola çıkacak zaten. PARA HARCASIN. Testi seçersek Geliver'a uğramasın."

Bu dosyanın koruduğu SEKİZ şey — her biri sessiz bir yanlışın karşılığı:

 1. Tek tık gerçekten para harcar (`dryRun` varsayılanı FALSE). Varsayılan
    kuru prova olsaydı ekran "gönderildi" derken paket yerinde dururdu.
 2. TEST yolu Geliver ucuna HİÇ gitmez. Karışsaydı "test" diye basılan düğme
    para harcardı.
 3. Takip numarası BAŞLIKTAN okunur; gövde PDF olduğu için oraya sığmaz.
 4. Otomatik basılan belge sayısı İKİ: etiket ve fatura. Teslim fişi YOK.
 5. Etiket ancak SATIN ALINDIKTAN sonra basılır; PDF gelmediyse kâğıt çıkmaz.
 6. Yazıcı yoksa iş DURMAZ: dosyalar diske yazılır, hata satırda yazılıdır.
 7. Aynı gönderi ikinci kez KENDİLİĞİNDEN basılmaz.
 8. Gerekçe boş bırakılabilir ve akış durmaz; defter yine dolu kalır.

Ağa çıkmaz, gerçek DB kullanmaz, canlıya yazmaz.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_shipping_backend import service as service_module
from store_shipping_backend.service import AUTO_PRINT_ACTION, ShippingService, dispatch_info
from store_shipping_fakes import Events, FakeApi, FakeLog, FakeStore

SIPARIS: dict[str, Any] = {
    "id": 91, "increment_id": "S-91", "status": "processing", "grand_total": "500.00",
    "total_qty_ordered": 3, "total_qty_invoiced": 3, "total_qty_shipped": 0,
    "customer_full_name": "Ayşe Yılmaz",
    "shipping_address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
    "items": [{"id": 42, "sku": "A", "qty_ordered": 3, "qty_shipped": 0}],
}

FATURA = [{"id": 19, "orderIncrementId": "S-91", "state": "paid"}]


class FakePrinter:
    """Yazıcı yeteneğinin testlik yüzü. `hazir=False` iken basmayı REDDEDER."""

    def __init__(self, *, hazir: bool = True) -> None:
        self.printed: list[Path] = []
        self.hazir = hazir

    async def print_file(self, path: Path, **_kwargs: Any) -> dict[str, Any]:
        if not self.hazir:
            raise RuntimeError("Yazıcı bağlantısı hatası: kuyruk kapalı.")
        self.printed.append(path)
        return {"printer": "HP LaserJet"}

    async def status(self) -> dict[str, Any]:
        return {"ready": self.hazir, "error": "" if self.hazir else "Yazıcı kapalı."}


def _service(tmp_path: Path, *, printer: Any = None, events: Any = None,
             **config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api, store = FakeApi(), FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(), notifier=None, publish=events, printer=printer,
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=tmp_path,
    )
    api.order_by_id = {91: dict(SIPARIS)}
    api.invoice_rows = list(FATURA)
    return service, api, store


def _doc(result: dict[str, Any], kind: str) -> dict[str, Any]:
    """Sonuçtaki belge satırı. Yoksa testin hangi belgeyi aradığını SÖYLER."""
    for item in result.get("documents") or []:
        if item.get("kind") == kind:
            return item
    raise AssertionError(f"{kind} belgesi sonuçta yok: {result.get('documents')}")


def _cleanup(result: dict[str, Any]) -> None:
    for item in result.get("documents") or []:
        if item.get("path"):
            Path(item["path"]).unlink(missing_ok=True)


# ============================================== 1 · tek tık gerçekten gönderir

async def test_kargoya_ver_tek_cagrida_gonderir_ve_takip_kodu_doner(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert result["dispatched"] is True
    assert result["trackingNo"] == "1234567890"     # ELLE GİRİLMEDİ, zincirin çıktısı
    assert result["shipmentId"] == 77
    assert result["purchased"] is True
    assert len(api.used("bbd_dispatch_order")) == 1
    _cleanup(result)


async def test_ARA_ONAY_yok_dry_run_varsayilani_KAPALI(tmp_path: Path) -> None:
    # Varsayılan kuru prova olsaydı ekran "gönderildi" derken paket yerinde
    # dururdu ve bu ancak müşteri aradığında fark edilirdi.
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali")

    assert api.used("bbd_dispatch_order")[0]["dry_run"] is False
    assert result["dryRun"] is False
    _cleanup(result)


async def test_kuru_prova_yetenegi_KODDA_DURUR(tmp_path: Path) -> None:
    # İstenirse hâlâ prova yapılabilir; yalnız varsayılan akışta kullanılmaz.
    service, api, _ = _service(tmp_path, auto_print=True)
    api.dispatch_envelope = {
        "contentType": "application/json", "status": 200, "headers": {},
        "content": b"", "json": {"labelReady": False, "message": "Kuru prova."},
    }
    result = await service.dispatch(91, actor="Ali", dry_run=True)

    assert api.used("bbd_dispatch_order")[0]["dry_run"] is True
    assert result["dryRun"] is True
    assert result["printed"] == []
    assert "Kuru prova" in result["printSkipped"]
    _cleanup(result)


# ================================================ 2 · TEST yolu Geliver'a uğramaz

async def test_test_yolu_KARGOYA_VER_ile_de_geliver_ucuna_gitmez(tmp_path: Path) -> None:
    # `test_test_yolu_geliver_ucuna_HIC_gitmez` sihirbaz yolunu koruyor;
    # bu da tek tık yolunu. İkisi ayrı kapı, ikisi de kapalı olmalı.
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali", provider="bagisto")

    assert result["ok"] is True
    assert api.used("bbd_dispatch_order") == []     # GELİVER UCU HİÇ ÇAĞRILMADI
    assert api.used("create_shipment")              # Bagisto'nun kendi ucu
    assert result["documents"] == [] and result["printed"] == []


async def test_ayardaki_test_yolu_da_geliver_ucuna_gitmez(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path, provider="bagisto")
    await service.dispatch(91, actor="Ali")
    assert api.used("bbd_dispatch_order") == []


async def test_taninmayan_yol_sessizce_GERCEGE_dusmez(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali", provider="gelivr")

    assert result["ok"] is False
    assert api.used("bbd_dispatch_order") == [] and api.used("create_shipment") == []


# ================================================== 3 · künye başlıklardan okunur

def test_takip_numarasi_BASLIKTAN_okunur_govde_PDF_oldugu_icin() -> None:
    info = dispatch_info({
        "contentType": "application/pdf", "status": 200,
        "headers": {"x-bbd-tracking-number": "7788990011", "x-bbd-shipment-id": "42",
                    "x-bbd-provider": "SURAT", "x-bbd-purchased": "true"},
        "content": b"%PDF-1.4 sahte", "json": None,
    })
    assert info["trackingNo"] == "7788990011"
    assert info["shipmentId"] == 42
    assert info["provider"] == "SURAT"
    assert info["purchased"] is True and info["labelReady"] is True


def test_purchased_bayragi_UC_YAZIMDA_da_taninir() -> None:
    # `1`, `true`, `yes` — birini tanıyıp diğerini tanımamak "etiket satın
    # alınmadı" diyen sessiz bir yanlış üretirdi.
    for yazim in ("1", "true", "YES", "evet"):
        info = dispatch_info({"headers": {"x-bbd-purchased": yazim}, "content": b"", "json": {}})
        assert info["purchased"] is True, yazim


def test_takip_numarasi_UYDURULMAZ() -> None:
    # Sipariş numarasını takip numarası diye göstermek, müşteriye çalışmayan
    # bir sorgu kodu vermek olurdu.
    info = dispatch_info({"headers": {}, "content": b"", "json": {"shipmentId": 5}})
    assert info["trackingNo"] == ""
    assert any("Takip numarası" in line for line in info["warnings"])


def test_PDF_olmayan_govde_etiket_SAYILMAZ() -> None:
    # 406/500 gövdesi de `application/pdf` başlığıyla gelebiliyor.
    info = dispatch_info({"contentType": "application/pdf",
                          "content": b'{"type":"/errors/406"}', "json": None, "headers": {}})
    assert info["label"] == b""
    assert info["labelReady"] is False


# ============================================ 4 · İKİ belge basılır, fiş YOK

async def test_otomatik_basimda_YALNIZ_etiket_ve_fatura_cikar(tmp_path: Path) -> None:
    # Kullanıcı "fiş yok" dedi: `handover` (kargoya teslim fişi) otomatik
    # akıştan çıkarıldı, kodu duruyor ve elle basılabiliyor.
    yazici = FakePrinter()
    service, _, _ = _service(tmp_path, printer=yazici)
    result = await service.dispatch(91, actor="Ali")

    assert [item["kind"] for item in result["documents"]] == ["label", "invoice"]
    assert sorted(result["printed"]) == ["invoice", "label"]
    assert len(yazici.printed) == 2
    assert all("teslim" not in path.name for path in yazici.printed)
    _cleanup(result)


async def test_handover_belgesi_ELLE_hala_basilabilir(tmp_path: Path) -> None:
    # "Kod kalsın, elle basılabilsin" — otomatik akıştan çıkmak silinmek değil.
    service, _, _ = _service(tmp_path)
    result = await service.build_report("handover", {"orderId": 91, "trackNumber": "T-1"})
    assert result["ok"] is True
    Path(result["path"]).unlink(missing_ok=True)


async def test_fatura_alinamazsa_ETIKET_YINE_BASILIR(tmp_path: Path) -> None:
    yazici = FakePrinter()
    service, api, _ = _service(tmp_path, printer=yazici)
    api.fail.add("invoice_pdf")
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert result["printed"] == ["label"]
    fatura = _doc(result, "invoice")
    assert fatura["error"] and fatura["path"] == ""
    _cleanup(result)


# ======================================= 5 · etiket ancak satın alınınca basılır

async def test_etiket_gelmediyse_KAGIT_CIKMAZ_ama_sessiz_kalinmaz(tmp_path: Path) -> None:
    yazici = FakePrinter()
    service, api, _ = _service(tmp_path, printer=yazici)
    api.dispatch_envelope = {
        "contentType": "application/json", "status": 200,
        "headers": {"x-bbd-shipment-id": "77", "x-bbd-tracking-number": "1234567890"},
        "content": b"", "json": {"labelReady": False, "message": "Etiket indirilemedi."},
    }
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert result["labelReady"] is False
    assert "label" not in [item["kind"] for item in result["documents"]]
    assert "label" not in result["printed"]
    assert any("Etiket indirilemedi" in line for line in result["warnings"])
    _cleanup(result)


# ============================================ 6 · yazıcı yoksa iş durmaz (K7)

async def test_yazici_yoksa_dosyalar_DISKE_yazilir_is_durmaz(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, printer=None)
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True and result["dispatched"] is True
    etiket = _doc(result, "label")
    assert Path(etiket["path"]).exists()
    assert etiket["printed"] is False
    assert "Yazıcı yeteneği" in etiket["printError"]
    _cleanup(result)


async def test_yazici_hazir_degilse_hata_SATIRDA_yazili_kalir(tmp_path: Path) -> None:
    yazici = FakePrinter(hazir=False)
    service, _, _ = _service(tmp_path, printer=yazici)
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert result["printed"] == []
    assert all(item["printError"] for item in result["documents"] if item["path"])
    _cleanup(result)


async def test_belgeler_0600_yazilir(tmp_path: Path) -> None:
    # Alıcı adı, adres ve telefon taşıyorlar.
    service, _, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali")
    for item in result["documents"]:
        if item["path"]:
            assert oct(Path(item["path"]).stat().st_mode)[-3:] == "600"
    _cleanup(result)


# ================================================= 7 · çift basım koruması

async def test_ayni_gonderi_IKINCI_KEZ_kendiliginden_basilmaz(tmp_path: Path) -> None:
    # İki tık ya da yinelenen istek aynı etiketi iki kez bastırırdı; iki koli
    # hazırlanır, biri fazla kargoya çıkardı.
    yazici = FakePrinter()
    service, _, _ = _service(tmp_path, printer=yazici)

    ilk = await service.dispatch(91, actor="Ali")
    ikinci = await service.dispatch(91, actor="Ali")

    assert len(ilk["printed"]) == 2
    assert ikinci["printed"] == []
    assert "daha önce otomatik basıldı" in ikinci["printSkipped"]
    assert len(yazici.printed) == 2          # toplam, ikinci turda artmadı
    _cleanup(ilk)
    _cleanup(ikinci)


async def test_tekrar_yazdir_cift_basim_kapisini_HIC_gormez(tmp_path: Path) -> None:
    # Kasıtlı tekrar ile kazara ikinci basım ayrı şeylerdir.
    yazici = FakePrinter()
    service, _, _ = _service(tmp_path, printer=yazici)
    result = await service.dispatch(91, actor="Ali")
    etiket = _doc(result, "label")

    tekrar = await service.print_report(etiket["path"])
    assert tekrar["ok"] is True
    assert len(yazici.printed) == 3
    _cleanup(result)


async def test_otomatik_basim_denetim_defterine_yazilir(tmp_path: Path) -> None:
    service, _, store = _service(tmp_path, printer=FakePrinter())
    result = await service.dispatch(91, actor="Ali")

    assert AUTO_PRINT_ACTION in [row["action"] for row in store.audit]
    _cleanup(result)


async def test_ayarla_kapatilan_otomatik_basim_YAZICIYA_gitmez(tmp_path: Path) -> None:
    yazici = FakePrinter()
    service, _, _ = _service(tmp_path, printer=yazici, auto_print=False)
    result = await service.dispatch(91, actor="Ali")

    assert result["autoPrint"] is False
    assert yazici.printed == []
    # Belge YİNE üretilir: kapalı olan basım, üretim değil.
    assert [item["kind"] for item in result["documents"]] == ["label", "invoice"]
    assert Path(result["documents"][0]["path"]).exists()
    _cleanup(result)


async def test_ekran_tercihi_ayari_ezer(tmp_path: Path) -> None:
    yazici = FakePrinter()
    service, _, _ = _service(tmp_path, printer=yazici, auto_print=True)
    await service.save_settings(auto_print=False, reason="Otomatik basım kapatıldı",
                                actor="Ali")

    assert (await service.settings())["autoPrint"] is False
    result = await service.dispatch(91, actor="Ali")
    assert yazici.printed == []
    _cleanup(result)


# ================================================== 8 · gerekçe akışı durdurmaz

async def test_gerekce_bos_birakilabilir_akis_DURMAZ(tmp_path: Path) -> None:
    service, api, store = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali", reason="")

    assert result["ok"] is True
    yazilan = api.used("bbd_dispatch_order")[0]["reason"]
    assert yazilan == service_module.dispatch_reason("91")
    # Geçit en az 10 karakter istiyor; otomatik metin bunu geçmeli.
    assert len(yazilan) >= 20
    assert any(row["action"] == "dispatch" and row["reason"] for row in store.audit)
    _cleanup(result)


async def test_elle_yazilan_gerekce_KORUNUR(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali", reason="Müşteri acele istedi, elden")

    assert api.used("bbd_dispatch_order")[0]["reason"] == "Müşteri acele istedi, elden"
    _cleanup(result)


# ==================================================== gövde ve ayakta kalma

async def test_bos_tasiyici_gonderilmez_musterinin_firmasi_secilsin(tmp_path: Path) -> None:
    # Boş dize göndermek, "müşterinin ödediği firmayı bul" tercihini
    # "hiçbir firma" diye okutabilirdi.
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali")

    body = api.used("bbd_dispatch_order")[0]["payload"]
    assert "carrier" not in body and "offerId" not in body
    assert body["packages"] == 1 and body["payer"] == "sender"
    _cleanup(result)


async def test_secilen_tasiyici_ve_teklif_govdeye_girer(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali", carrier="Hepsijet", offer_id="of-9",
                                    desi_value=2.5, weight=1.2, packages=2,
                                    payer="receiver", cod=12345, note="Kapıya bırak")

    body = api.used("bbd_dispatch_order")[0]["payload"]
    assert body["carrier"] == "hepsijet" and body["offerId"] == "of-9"
    assert body["desi"] == 2.5 and body["weight"] == 1.2 and body["packages"] == 2
    assert body["payer"] == "receiver" and body["codAmount"] == "123.45"
    assert body["note"] == "Kapıya bırak"
    _cleanup(result)


async def test_magaza_dusunce_ekran_cokmez(tmp_path: Path) -> None:
    service, api, store = _service(tmp_path)
    api.fail.add("bbd_dispatch_order")
    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is False and result["error"]
    # "Ne yapmaya çalıştık" kaydı YERELDE kalır: istek uzakta uygulanmış olabilir.
    assert [row["result"] for row in store.audit if row["action"] == "dispatch"] \
        == ["denendi", "hata"]


async def test_olaylar_yayinlanir(tmp_path: Path) -> None:
    olaylar = Events()
    service, _, _ = _service(tmp_path, events=olaylar)
    result = await service.dispatch(91, actor="Ali")

    assert "store.shipment.created" in olaylar.names()
    assert "store.shipment.purchased" in olaylar.names()
    _cleanup(result)


async def test_etiket_satin_alinmadiysa_satin_alma_olayi_YAYINLANMAZ(tmp_path: Path) -> None:
    olaylar = Events()
    service, api, _ = _service(tmp_path, events=olaylar)
    api.dispatch_envelope = {
        "contentType": "application/json", "status": 200, "headers": {},
        "content": b"", "json": {"labelReady": False, "shipmentId": 77},
    }
    result = await service.dispatch(91, actor="Ali")

    assert "store.shipment.purchased" not in olaylar.names()
    _cleanup(result)
