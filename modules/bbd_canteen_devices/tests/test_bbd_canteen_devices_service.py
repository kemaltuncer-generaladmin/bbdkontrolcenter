"""Kantin Cihazları — iş kuralları.

ÜÇ ZORUNLU İDDİA bu dosyada durur:

  1. Eşleme kodu BİR KEZ yakılır (eş zamanlı iki istek).
  2. İptal edilmiş kioska kod üretilmez.
  3. İptal ayrı izin ister; izinsiz çağrı kantine HİÇ ULAŞMAZ.

Geri kalanı bu üçünün kenarlarıdır: gerekçe denetimi, yazma denemesinin izi ve
kantin düştüğünde ekranın ayakta kalması (K7).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from bbd_canteen_devices_backend.service import CanteenDeviceService
from canteen_devices_fakes import CanteenDenied, FakeBus, FakeCanteen, FakeLog, FakeStore

GEREKCE = "Kantin kiosk cihazi degistirildi"


def build(canteen: FakeCanteen | None = None) -> tuple[CanteenDeviceService, FakeCanteen,
                                                       FakeStore, FakeBus]:
    api = canteen or FakeCanteen()
    store = FakeStore()
    bus = FakeBus()
    service = CanteenDeviceService(canteen=api, store=store, log=FakeLog(),
                                   config={"online_after_minutes": 5,
                                           "pairing_ttl_minutes": 10},
                                   printer=None, publish=bus)
    return service, api, store, bus


async def open_kiosk(service: CanteenDeviceService, name: str = "Kantin Kiosk") -> dict:
    result = await service.create_kiosk(name=name, reason=GEREKCE, actor="Ayşe")
    assert result["ok"], result
    return result


# =========================================================== 1. TEK KULLANIM

async def test_es_zamanli_iki_kod_uretimi_tek_kullanilabilir_kod_birakir() -> None:
    """Kontrol Merkezi arkasında ÇALIŞAN İKİNCİ BİR KOD BIRAKMAZ.

    İki yönetici (ya da iki kez tıklayan bir yönetici) aynı anda kod üretirse
    ikisi de ekranda bir kod görür. Kantin yeni kodu yazarken eskisini
    geçersiz kılıyor; bu testin sabitlediği şey, o kuralın eş zamanlı çağrıda
    da tuttuğu ve GEÇERSİZ KALAN kodun gerçekten ölü olduğudur. Eskisi canlı
    kalsaydı, hiçbir ekranda görünmeyen ikinci bir açık kapı olurdu.
    """
    service, api, store, _ = build()
    opened = await open_kiosk(service)
    kiosk_id = opened["kiosk"]["id"]
    ilk_kod = opened["pairing"]["code"]

    birinci, ikinci = await asyncio.gather(
        service.pairing_code(kiosk_id, reason=GEREKCE, actor="Ayşe"),
        service.pairing_code(kiosk_id, reason=GEREKCE, actor="Veli"),
    )
    assert birinci["ok"] and ikinci["ok"]
    kodlar = {birinci["pairing"]["code"], ikinci["pairing"]["code"]}
    assert len(kodlar) == 2, "iki ayrı üretim aynı kodu döndürdü"

    # Kayıt açılışındaki kod da, birinci üretim de ÖLDÜ; yalnız sonuncusu yaşar.
    with pytest.raises(CanteenDenied):
        api.pair(ilk_kod)

    canli = [kod for kod in kodlar if _yasiyor(api, kod)]
    assert len(canli) == 1, f"birden fazla canlı kod kaldı: {canli}"

    # Her iki deneme de ize düştü: "kim ne zaman kod üretti" sorusunun cevabı
    # kantinde değil, burada durur.
    assert store.results("pairing_code").count("ok") == 2


def _yasiyor(api: FakeCanteen, code: str) -> bool:
    """Kod hâlâ eşleme yapabiliyor mu — yakmadan denenemez, bu yüzden yakar."""
    try:
        api.pair(code)
    except CanteenDenied:
        return False
    return True


async def test_ayni_kod_ikinci_kez_calismaz() -> None:
    """Kontrol Merkezi'nin ÜZERİNE KURULDUĞU sözleşme.

    Yakma kantinde olur (`UPDATE ... WHERE code_used_at IS NULL`) ve asıl kanıtı
    orada durur: `bbdkantin/backend/tests/Feature/KioskPairingTest.php`. Buradaki
    test o sözleşmeyi SABİTLER: "bir kod bir cihaz" varsayımı gevşerse taklit de
    gevşemek zorunda kalır ve bu satır kırmızıya döner — sessizce doğru sanılan
    bir varsayım kalmaz.
    """
    service, api, _, _ = build()
    opened = await open_kiosk(service)
    code = opened["pairing"]["code"]

    token = api.pair(code, device_name="Kantin Kiosk 1")
    assert token

    with pytest.raises(CanteenDenied):
        api.pair(code, device_name="Sahte Kiosk")

    # Tek kod, TEK token. İkinci istek sessizce ikinci bir cihaz açmadı.
    assert len(api.tokens) == 1


# ================================================== 2. İPTAL EDİLMİŞE KOD YOK

async def test_iptal_edilmis_kioska_kod_uretilmez() -> None:
    """Ayrı iznin ANLAMI budur.

    İptal `.devices` iznine, kod üretimi `.manage`e bağlı. İptal edilmiş bir
    kioska kod üretilebilseydi, yalnız `manage` taşıyan biri `.devices`
    taşıyanın kararını geri alır ve iptal edilen cihaz kantine dönerdi.
    """
    service, api, store, _ = build()
    opened = await open_kiosk(service)
    kiosk_id = opened["kiosk"]["id"]

    iptal = await service.revoke_kiosk(kiosk_id, reason=GEREKCE, actor="Ayşe",
                                       allow_destructive=True)
    assert iptal["ok"], iptal

    sonuc = await service.pairing_code(kiosk_id, reason=GEREKCE, actor="Veli")
    assert sonuc["ok"] is False
    assert "iptal" in sonuc["error"].lower()

    # KANTİNE HİÇ GİDİLMEDİ: kapı burada kapandı, ağ turu bile atılmadı.
    assert "new_kiosk_pairing_code" not in api.writes()
    assert store.results("pairing_code") == ["engellendi"]


async def test_iptal_edilmis_kiosk_bekleyen_koduyla_da_eslenemez() -> None:
    # İptal anında elde kalan kod da ölür; yaşasaydı iptal kararı o kod
    # girilene kadar geçerli görünür, sonra sessizce delinirdi.
    service, api, _, _ = build()
    opened = await open_kiosk(service)
    code = opened["pairing"]["code"]

    await service.revoke_kiosk(opened["kiosk"]["id"], reason=GEREKCE, actor="Ayşe",
                               allow_destructive=True)

    with pytest.raises(CanteenDenied):
        api.pair(code)


# ==================================================== 3. İPTAL AYRI İZİN İSTER

async def test_iptal_ayri_izin_ister_ve_izinsiz_cagri_kantine_ulasmaz() -> None:
    """K9'un çift kapısının SERVİS yarısı.

    Uç noktada da `requires(DEVICES)` var (bkz. rota testi). Burada tekrar
    denetlenmesinin sebebi, izin kararının HTTP katmanı ayağa kaldırılmadan
    sınanabilir olması gerektiğidir.
    """
    service, api, store, bus = build()
    opened = await open_kiosk(service)

    sonuc = await service.revoke_kiosk(opened["kiosk"]["id"], reason=GEREKCE,
                                       actor="Yetkisiz", allow_destructive=False)
    assert sonuc["ok"] is False
    assert "bbd_canteen_devices.devices" in sonuc["error"]

    assert "revoke_kiosk" not in api.writes()
    assert store.results("revoke_kiosk") == ["engellendi"]
    # Olmayan bir iptali duyurmak, dinleyicileri yalanla uyandırırdı.
    assert bus.names() == []


async def test_iptal_token_i_dusurur_ve_olayi_duyurur() -> None:
    service, api, store, bus = build()
    opened = await open_kiosk(service)
    kiosk_id = opened["kiosk"]["id"]

    sonuc = await service.revoke_kiosk(kiosk_id, reason=GEREKCE, actor="Ayşe",
                                       allow_destructive=True)
    assert sonuc["ok"] and sonuc["kiosk"]["revoked"] is True
    assert api.used("revoke_kiosk") == [{"reason": GEREKCE}]

    assert ("canteen.kiosk_revoked", {
        "kioskId": kiosk_id, "name": "Kantin Kiosk", "reason": GEREKCE, "actor": "Ayşe",
    }) in bus.events

    # İz: önce "denendi", sonra "ok". Ağ koparsa geriye YALNIZ ilki kalır.
    assert store.results("revoke_kiosk") == ["denendi", "ok"]


async def test_zaten_iptal_edilmis_kiosk_ikinci_kez_iptal_edilmez() -> None:
    service, api, _, _ = build()
    opened = await open_kiosk(service)
    kiosk_id = opened["kiosk"]["id"]

    await service.revoke_kiosk(kiosk_id, reason=GEREKCE, actor="Ayşe",
                               allow_destructive=True)
    ikinci = await service.revoke_kiosk(kiosk_id, reason=GEREKCE, actor="Ayşe",
                                        allow_destructive=True)
    assert ikinci["ok"] is False
    assert api.writes().count("revoke_kiosk") == 1


# ================================================================== kenarlar

async def test_gerekce_kisaysa_kantine_gidilmez() -> None:
    # Arayüzde alanı zorunlu göstermek yetkilendirme değildir (K9); istemci
    # gövdeyi elle kurabilir.
    service, api, _, _ = build()
    sonuc = await service.create_kiosk(name="Kiosk", reason="kisa", actor="Ayşe")
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_ayni_adli_ikinci_kiosk_acilmaz() -> None:
    # Aynı adlı iki cihaz, iptal düğmesine basan kişiyi yazı tura atmaya bırakır.
    service, api, store, _ = build()
    await open_kiosk(service, "Kantin Kiosk")

    sonuc = await service.create_kiosk(name="kantin kiosk", reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is False
    assert api.writes().count("create_kiosk") == 1
    assert "engellendi" in store.results("create_kiosk")


async def test_kod_denetim_izine_yazilmaz() -> None:
    """Kod bir sırdır; iz satırı ise silinmez.

    Koda ize yazsaydık, 10 dakikalık kodun ömrü izi okuyabilen herkes için
    sonsuz olurdu.
    """
    service, _, store, _ = build()
    opened = await open_kiosk(service)
    code = opened["pairing"]["code"]

    yazilan = " ".join(row["detail"] for row in store.audit)
    assert code not in yazilan


async def test_kantin_dustugunde_ekran_ayakta_kalir() -> None:
    # K7: uç 200 döner, `connected` False olur ve panel nedenini yazar. Boş bir
    # liste "kiosk yok" ile "kantine ulaşılamıyor"u aynı gösterirdi.
    service, api, _, _ = build()
    api.fail.add("kiosks")

    sonuc = await service.overview()
    assert sonuc["ok"] is True
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    assert sonuc["error"]
    # Sözleşme YEREL: geçit düşse bile panel düğmelerini çizebilir.
    assert sonuc["printer_available"] is False
    assert sonuc["pairing_ttl_minutes"] == 10


async def test_eslenen_kiosk_bir_kez_duyurulur() -> None:
    """`canteen.device_enrolled` — manifestte ilan edilmiş, bugüne dek hiç
    yayınlanmamış olay.

    Eşlemeyi Kontrol Merkezi başlatmıyor: kodu cihaz giriyor ve kantin haber
    vermiyor. Olay bu yüzden listeye bakarken FARK EDİLEREK doğar. İki kural
    birden sınanır: ilk okumada zaten eşli olan kiosk "yeni eşlendi" sayılmaz
    (yoksa modülün ilk açılışı sahadaki her cihazı ilan ederdi) ve aynı eşleme
    ikinci kez duyurulmaz.
    """
    service, api, _, bus = build()
    opened = await open_kiosk(service)
    kiosk_id = opened["kiosk"]["id"]

    # İlk okuma: kiosk henüz eşlenmemiş — duyurulacak bir şey yok.
    await service.overview()
    assert bus.names() == []

    api.pair(opened["pairing"]["code"], device_name="Kantin Kiosk", platform="android",
             app_version="1.4.0")

    await service.overview()
    assert bus.names() == ["canteen.device_enrolled"]
    _, payload = bus.events[0]
    assert payload["kioskId"] == kiosk_id
    assert payload["platform"] == "android"
    assert payload["appVersion"] == "1.4.0"

    # İkinci okuma AYNI olayı tekrarlamaz.
    await service.overview()
    assert bus.names() == ["canteen.device_enrolled"]


async def test_ilk_okumada_zaten_esli_kiosk_duyurulmaz() -> None:
    service, api, _, bus = build()
    opened = await open_kiosk(service)
    api.pair(opened["pairing"]["code"])

    # Modül ilk kez açılıyor (hatıra tablosu boş) ve kiosk zaten eşli.
    await service.overview()
    assert bus.names() == []


async def test_iz_yazilamazsa_is_durmaz() -> None:
    # K7: denetim izi yazılamadı diye kiosk açılmaması, kaydın hiç açılmaması
    # demek olurdu; iz bir yan kayıttır, işin kendisi değil.
    service, api, store, _ = build()
    store.broken = True

    sonuc = await service.create_kiosk(name="Kiosk", reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert "create_kiosk" in api.writes()


async def test_ad_degismiyorsa_kantine_istek_gitmez() -> None:
    service, api, _, _ = build()
    opened = await open_kiosk(service, "Kantin Kiosk")

    sonuc = await service.rename_kiosk(opened["kiosk"]["id"], name="Kantin Kiosk",
                                       reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert sonuc["changed"] is False
    assert "rename_kiosk" not in api.writes()


# ======================================== 4. EŞLEME FİŞİ: sunucu üretir, cihaz basar


async def test_eslesme_fisi_URETILIR_sunucu_basmaz(tmp_path) -> None:
    """Fiş dosyası üretilir ve yolu döner; kâğıdı ekran çıkarır (ADR 0026).

    Eskiden burada `printer.print_file` çağrılıyordu. Çekirdek sunucuya
    taşındıktan sonra o çağrı hep düşüyordu — sunucu imajında CUPS yok, yazıcı
    kullanıcının masasında — ve "Fiş bas" işaretlenmiş olsa bile kâğıt hiç
    çıkmıyordu. Yetenek YOKKEN de fişin üretilmesi gerekir; sunucuda hiçbir
    zaman olmayacak.
    """
    service, _, _, _ = build()
    service._fallback_dir = tmp_path / "exports"
    opened = await open_kiosk(service)

    result = await service.pairing_code(opened["kiosk"]["id"], reason=GEREKCE,
                                        actor="Ayşe", print_slip=True)

    assert result["ok"] is True
    slip = result["print"]
    assert slip["error"] == ""
    # "Basıldı" DENMEZ: kâğıt henüz çıkmadı, bunu ekran söyleyecek.
    assert slip["printed"] is False

    uretilen = Path(slip["path"])
    assert uretilen.is_file()
    assert uretilen.read_bytes().startswith(b"%PDF")
    # Fiş tek kullanımlık kodu taşıyor; klasör ve dosya dar izinle açılır.
    assert oct(uretilen.stat().st_mode)[-3:] == "600"


async def test_fis_istenmezse_dosya_URETILMEZ(tmp_path) -> None:
    # Kod ekrandan okunacaksa diske tek kullanımlık kod yazmanın anlamı yok.
    service, _, _, _ = build()
    service._fallback_dir = tmp_path / "exports"
    opened = await open_kiosk(service)

    result = await service.pairing_code(opened["kiosk"]["id"], reason=GEREKCE,
                                        actor="Ayşe", print_slip=False)

    assert result["ok"] is True
    assert result["print"] == {}
    assert not list(tmp_path.rglob("*.pdf"))
