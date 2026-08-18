"""Yazıcı yeteneği — CUPS çıktısının okunması.

Buradaki dizgeler UYDURMA DEĞİL: bu makinedeki gerçek `lpstat` çıktılarından
alındı. İki gerçek hata bu testlerle kapandı:

1. Yazıcı baskı yaparken satır "is idle" değil "now printing" oluyor; ilk
   desen eşleşmiyor, durum "bilinmiyor" sanılıp yazıcı hazır değil sayılıyordu.
2. Baskı isteğinde kâğıt boyutu bildirilmiyordu; kuyruğun varsayılanı (bu
   makinede A6) geçerli oluyor ve yazıcı A4 belgeyi alınca kâğıt uyuşmazlığı
   hatası veriyordu.
"""

import asyncio

import pytest

from km_platform.printer.cups import (
    PrinterError,
    PrinterService,
    _parse_accepting,
    _parse_states,
)

# Gerçek çıktı: yazıcı boşta.
BOSTA = """printer 80mm is idle.  enabled since Wed Aug 12 02:43:48 2026
printer EPSON_M2170_Series is idle.  enabled since Thu Aug 13 11:49:26 2026
printer HP_LaserJet_MFP_M139-M142 is idle.  enabled since Thu Aug 13 14:06:50 2026
"""

# Gerçek çıktı: yazıcı BASKI YAPIYOR. Alt satır filtrenin kendi mesajı.
MESGUL = """printer HP_LaserJet_MFP_M139-M142 now printing HP_LaserJet_MFP_M139-M142-17.  \
enabled since Thu Aug 13 14:06:41 2026
\tcfFilterGhostscript: Rendering completed
"""

DEVRE_DISI = "printer HP_LaserJet_MFP_M139-M142 disabled since Thu Aug 13 14:00:00 2026 -\n"


def test_bosta_durumu_okunur() -> None:
    states = _parse_states(BOSTA)
    assert states["HP_LaserJet_MFP_M139-M142"] == "boşta"
    assert states["80mm"] == "boşta"


def test_baski_yaparken_durum_okunur() -> None:
    # ESKİ HATA: bu satır eşleşmiyor, durum "bilinmiyor" oluyor ve yazıcı
    # hazır değil sayılıyordu. Kullanıcı normal bir belge bastıktan hemen
    # sonra uygulamadan yazdırınca "yazıcı bağlantısı hatası" alıyordu.
    states = _parse_states(MESGUL)
    assert states["HP_LaserJet_MFP_M139-M142"] == "baskı yapıyor"


def test_filtre_alt_satirlari_yazici_sanilmaz() -> None:
    states = _parse_states(MESGUL)
    assert list(states) == ["HP_LaserJet_MFP_M139-M142"]


def test_devre_disi_durumu_okunur() -> None:
    states = _parse_states(DEVRE_DISI)
    assert states["HP_LaserJet_MFP_M139-M142"] == "devre dışı"


def test_kabul_durumu_okunur() -> None:
    text = ("HP_LaserJet_MFP_M139-M142 accepting requests since Thu Aug 13 14:06:50 2026\n"
            "80mm not accepting requests since Wed Aug 12 02:43:48 2026\n")
    accepting = _parse_accepting(text)
    assert accepting["HP_LaserJet_MFP_M139-M142"] is True
    assert accepting["80mm"] is False


class FakeConfig:
    def __init__(self, values: dict) -> None:
        self._values = values

    def section(self, _name: str) -> dict:
        return self._values


@pytest.fixture
def service() -> PrinterService:
    return PrinterService(FakeConfig({"default_printer": "HP_LaserJet_MFP_M139-M142"}), _Log())


class _Log:
    def info(self, *_args, **_kwargs) -> None: ...
    def warning(self, *_args, **_kwargs) -> None: ...


def test_kagit_boyutu_varsayilani_a4(service: PrinterService) -> None:
    # Raporlar A4; kuyruğun varsayılanına GÜVENİLMEZ (bu makinede A6'ydı).
    assert service._media == "A4"


def test_kagit_boyutu_ayardan_ezilebilir() -> None:
    service = PrinterService(FakeConfig({"media": "Letter"}), _Log())
    assert service._media == "Letter"


def test_bosluk_birakilan_kagit_ayari_a4e_duser() -> None:
    service = PrinterService(FakeConfig({"media": "   "}), _Log())
    assert service._media == "A4"


def test_bosluk_birakilan_esleme_ayari_varsayilana_duser() -> None:
    service = PrinterService(FakeConfig({"usb_match": "  "}), _Log())
    assert service._match == "laserjet"


@pytest.mark.asyncio
async def test_baski_komutu_kagit_boyutunu_iki_adla_verir(tmp_path, monkeypatch) -> None:
    """`media` ile `PageSize` birlikte gider.

    Yalnız `media` verildiğinde PPD tabanlı sürücü kullanıcının
    `~/.cups/lpoptions` dosyasındaki `PageSize`i kullanmaya devam edebiliyor;
    bu makinede orada A6 yazıyordu ve yazıcı hata veriyordu.
    """
    service = PrinterService(FakeConfig({"media": "A4"}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str):
        calls.append(args)
        return 0, "request id is HP_X-1 (1 file(s))", ""

    monkeypatch.setattr(service, "_tools", lambda: ("/usr/bin/lp", "/usr/bin/lpstat"))
    monkeypatch.setattr(service, "_run", fake_run)

    async def fake_target():
        return {"name": "HP_X", "uri": "hp:/usb/HP_X", "usb": True,
                "state": "boşta", "accepting": True, "ready": True}

    monkeypatch.setattr(service, "target", fake_target)

    result = await service.print_file(pdf, title="rapor", copies=1)

    assert result["ok"] is True
    assert result["media"] == "A4"
    komut = calls[0]
    assert "media=A4" in komut
    assert "PageSize=A4" in komut


# ---------------------------------------------------------------- kuyruk seçimi
#
# Buradaki kurgu GERÇEK BİR ARIZADAN alındı: aynı fiziksel yazıcının iki
# kuyruğu vardı ve `ipp-usb` USB aygıtını tekeline aldığı için klasik HPLIP
# kuyruğu cihaza ulaşamıyordu. CUPS yine de işi kabul edip "tamamlandı"
# diyordu — yazılımın gördüğü her şey başarılıydı, kâğıt çıkmıyordu.

KLASIK = "HP_LaserJet_MFP_M139-M142"
SURUCUSUZ = "HP_LaserJet_MFP_M141w_BE4507_USB"

URILER = {
    KLASIK: "hp:/usb/HP_LaserJet_MFP_M139-M142?serial=VNFP119766",
    SURUCUSUZ: "ipp://HP%20LaserJet%20MFP%20M141w%20(BE4507)%20(USB)._ipp._tcp.local/",
    "EPSON_M2170_Series": "implicitclass://EPSON_M2170_Series/",
    "80mm": "usb://KODPOS/80mm%20Series?serial=11101800002",
}


def _kurulum(monkeypatch, service: PrinterService, *, ippusb: int | None) -> None:
    """`lpstat` çağrılarını gerçek çıktı kalıplarıyla taklit eder."""
    async def fake_run(*args: str):
        if args[-1] == "-v":
            return 0, "".join(f"device for {n}: {u}\n" for n, u in URILER.items()), ""
        if args[-1] == "-p":
            return 0, "".join(f"printer {n} is idle.  enabled since Thu\n" for n in URILER), ""
        if args[-1] == "-a":
            return 0, "".join(f"{n} accepting requests since Thu\n" for n in URILER), ""
        return 0, "", ""

    async def fake_port():
        return ippusb

    async def fake_info():
        if ippusb is None:
            return {}
        # ipp-usb yalnız HP'yi sahiplenir; fiş yazıcısı ondan etkilenmez.
        return {"port": ippusb, "printer-make-and-model": "HP LaserJet MFP M139-M142"}

    monkeypatch.setattr(service, "_tools", lambda: ("/usr/bin/lp", "/usr/bin/lpstat"))
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service, "device_port", fake_port)
    monkeypatch.setattr(service, "device_info", fake_info)


async def test_ippusb_ayaktayken_surucusuz_usb_kuyrugu_secilir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    chosen = await service.target()
    assert chosen["name"] == SURUCUSUZ
    assert chosen["driverless"] is True


async def test_ippusb_ayaktayken_klasik_kuyruk_tuzak_sayilir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    printers = {p["name"]: p for p in await service.discover()}
    # Kuyruk "boşta" ve "iş kabul ediyor" görünüyor ama cihaza ulaşamıyor.
    assert printers[KLASIK]["trapped"] is True
    assert printers[KLASIK]["ready"] is False
    assert printers[SURUCUSUZ]["ready"] is True


async def test_ippusb_yokken_klasik_usb_kuyrugu_secilir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=None)

    chosen = await service.target()
    assert chosen["name"] == KLASIK
    assert chosen["trapped"] is False


async def test_gercek_ag_yazicisi_usb_sanilmaz(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    printers = {p["name"]: p for p in await service.discover()}
    # EPSON sürücüsüz ama adında USB damgası yok — ağ yazıcısıdır.
    assert printers["EPSON_M2170_Series"]["usb"] is False


async def test_termal_yazici_laserjet_esleşmesiyle_elenir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=None)

    chosen = await service.target()
    assert chosen["name"] != "80mm"


async def test_ayardaki_kuyruk_baglayicidir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({"default_printer": SURUCUSUZ}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    chosen = await service.target()
    assert chosen["name"] == SURUCUSUZ


async def test_ayarda_tuzak_kuyruk_secilirse_reddedilir(monkeypatch) -> None:
    # Yönetici elle yanlış kuyruğu sabitlerse sessizce kabul edilmez.
    service = PrinterService(FakeConfig({"default_printer": KLASIK}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    with pytest.raises(PrinterError, match="ipp-usb"):
        await service.target()


async def test_yalniz_tuzak_kuyruk_varsa_neden_anlatilir(monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())

    async def fake_run(*args: str):
        if args[-1] == "-v":
            return 0, f"device for {KLASIK}: {URILER[KLASIK]}\n", ""
        if args[-1] == "-p":
            return 0, f"printer {KLASIK} is idle.  enabled since Thu\n", ""
        if args[-1] == "-a":
            return 0, f"{KLASIK} accepting requests since Thu\n", ""
        return 0, "", ""

    async def fake_port():
        return 60000

    async def fake_info():
        return {"port": 60000, "printer-make-and-model": "HP LaserJet MFP M139-M142"}

    monkeypatch.setattr(service, "_tools", lambda: ("/usr/bin/lp", "/usr/bin/lpstat"))
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service, "device_port", fake_port)
    monkeypatch.setattr(service, "device_info", fake_info)

    with pytest.raises(PrinterError, match="kâğıt çıkmaz"):
        await service.target()


async def test_fis_yazicisi_ippusbden_etkilenmez(monkeypatch) -> None:
    """ipp-usb yalnız HP'yi sahiplenir; termal fiş yazıcısı çalışmaya devam eder.

    İleride fiş/etiket baskısı da bu yetenekten geçecek. Her klasik USB
    kuyruğunu tuzak saymak, o gün fiş yazıcısını sessizce devre dışı bırakırdı.
    """
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    printers = {p["name"]: p for p in await service.discover()}
    assert printers["80mm"]["trapped"] is False
    assert printers["80mm"]["ready"] is True
    assert printers[KLASIK]["trapped"] is True


async def test_cihaz_kimligi_okunamazsa_kimse_tuzak_sayilmaz(monkeypatch) -> None:
    # `ipptool` kurulu değilse cihazın kimliği bilinmez. O zaman suçlama
    # yapılmaz; koruma sürücüsüz kuyruğu YEĞLEMEKTEN gelir.
    service = PrinterService(FakeConfig({}), _Log())
    _kurulum(monkeypatch, service, ippusb=60000)

    async def kimliksiz():
        return {"port": 60000}

    monkeypatch.setattr(service, "device_info", kimliksiz)

    printers = {p["name"]: p for p in await service.discover()}
    assert printers[KLASIK]["trapped"] is False
    chosen = await service.target()
    assert chosen["name"] == SURUCUSUZ


# ---------------------------------------------------------------------------
# BİR TIKLAMA BİR BASKI
#
# Sahada yazıcı "aynı raporu basıp duruyor"du. Tetikleyici tek bir yer değildi:
# üst üste binen önizleme katmanları, çift tıklama, ağ yavaşken sabırsızlanan
# kullanıcı — hepsi aynı sonucu veriyor. Ortak noktaları bu kapıdan geçmeleri
# (K4), koruma da bu yüzden burada duruyor.
# ---------------------------------------------------------------------------


def _yazici_kurulumu(service: PrinterService, monkeypatch, calls: list) -> None:
    """`lp` çağrılarını toplayan, hedefi sabit bir kurulum."""

    async def fake_run(*args: str):
        calls.append(args)
        return 0, f"request id is HP_X-{len(calls)} (1 file(s))", ""

    async def fake_target():
        return {"name": "HP_X", "uri": "hp:/usb/HP_X", "usb": True,
                "state": "boşta", "accepting": True, "ready": True}

    monkeypatch.setattr(service, "_tools", lambda: ("/usr/bin/lp", "/usr/bin/lpstat"))
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service, "target", fake_target)


@pytest.mark.asyncio
async def test_ayni_belge_pencerede_ikinci_kez_gonderilmez(tmp_path, monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    ilk = await service.print_file(pdf, title="rapor")
    ikinci = await service.print_file(pdf, title="rapor")

    assert ilk["duplicate"] is False
    assert ikinci["duplicate"] is True
    # ASIL İDDİA: `lp` BİR KEZ çalıştı.
    assert len(calls) == 1
    # Kullanıcı ne olduğunu okuyabilmeli; sessizce yutulmaz.
    assert "ikinci kez gönderilmedi" in ikinci["note"]
    # İlk işin bilgisi korunur: ekran "hangi yazıcıya gitti" diyebilmeli.
    assert ikinci["printer"] == ilk["printer"]


@pytest.mark.asyncio
async def test_pencere_gectikten_sonra_yeniden_basilabilir(tmp_path, monkeypatch) -> None:
    """Koruma kasıtlı ikinci baskıyı ENGELLEMEZ; yalnız tek eylemi tekilleştirir."""
    service = PrinterService(FakeConfig({"duplicate_window_seconds": 0.01}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    await service.print_file(pdf, title="rapor")
    await asyncio.sleep(0.05)
    tekrar = await service.print_file(pdf, title="rapor")

    assert tekrar["duplicate"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_yeniden_URETILEN_rapor_ayri_belgedir(tmp_path, monkeypatch) -> None:
    """Aynı ada yeniden üretilen rapor BAŞKA belgedir ve basılabilmelidir.

    Damga yalnız yola bakmıyor; boyut ve değişim damgası da giriyor. Yoksa
    "raporu yeniden üret, sonra bas" akışı pencere boyunca ölürdü.
    """
    service = PrinterService(FakeConfig({}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    await service.print_file(pdf, title="rapor")
    pdf.write_bytes(b"%PDF-1.4\nyeni icerik uretildi\n")
    yeni = await service.print_file(pdf, title="rapor")

    assert yeni["duplicate"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_farkli_belge_engellenmez(tmp_path, monkeypatch) -> None:
    service = PrinterService(FakeConfig({}), _Log())
    bir = tmp_path / "bir.pdf"
    iki = tmp_path / "iki.pdf"
    bir.write_bytes(b"%PDF-1.4\nbir\n")
    iki.write_bytes(b"%PDF-1.4\niki\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    await service.print_file(bir, title="bir")
    sonuc = await service.print_file(iki, title="iki")

    assert sonuc["duplicate"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_basarisiz_gonderim_pencereyi_KILITLEMEZ(tmp_path, monkeypatch) -> None:
    """İlk deneme yazıcıyı bulamazsa kullanıcı hemen tekrar deneyebilmeli.

    Damga iş kuyruğa GİRDİKTEN sonra yazılır. Önce yazılsaydı, yazıcı o an
    kapalıysa kullanıcı pencere boyunca hiç basamaz ve nedenini anlamazdı.
    """
    service = PrinterService(FakeConfig({}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []

    async def fake_run(*args: str):
        calls.append(args)
        if len(calls) == 1:
            return 1, "", "lp: yazıcı yok"
        return 0, "request id is HP_X-2 (1 file(s))", ""

    async def fake_target():
        return {"name": "HP_X", "uri": "hp:/usb/HP_X", "usb": True,
                "state": "boşta", "accepting": True, "ready": True}

    monkeypatch.setattr(service, "_tools", lambda: ("/usr/bin/lp", "/usr/bin/lpstat"))
    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service, "target", fake_target)

    with pytest.raises(PrinterError):
        await service.print_file(pdf, title="rapor")
    sonuc = await service.print_file(pdf, title="rapor")

    assert sonuc["duplicate"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_koruma_ayardan_kapatilabilir(tmp_path, monkeypatch) -> None:
    service = PrinterService(FakeConfig({"duplicate_window_seconds": 0}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    await service.print_file(pdf, title="rapor")
    await service.print_file(pdf, title="rapor")

    assert len(calls) == 2

# ------------------------------------- "CUPS yok" ile "yazıcı arızalı" ayrımı


def test_cups_kurulu_degilse_installed_false(monkeypatch) -> None:
    """Sunucuda CUPS YOKTUR ve bu bir arıza değildir.

    Yetenek sunucuda da kayıtlı (`http/app.py`), yani ekran ona durum
    sorabiliyor. `status()` o durumda "sistemde CUPS (lp/lpstat) bulunamadı"
    diye HATA döndürüyor ve Sistem Ayarları'nı açan herkes, baskı düzgün
    çalışırken kırmızı bir "Yazıcı bağlantısı hatası" görüyordu — kâğıt
    kullanıcının makinesinden çıkıyor, sunucudan çıkması hiç beklenmiyor.
    Çağıranın bu iki hâli ayırt edebilmesi için ayrı bir soru var.
    """
    monkeypatch.setattr("km_platform.printer.cups.shutil.which", lambda _name: None)
    assert PrinterService.installed() is False


def test_cups_varsa_installed_true(monkeypatch) -> None:
    monkeypatch.setattr("km_platform.printer.cups.shutil.which",
                        lambda name: f"/usr/bin/{name}")
    assert PrinterService.installed() is True


def test_tek_arac_eksikse_kurulu_sayilmaz(monkeypatch) -> None:
    # `lp` var, `lpstat` yok: keşif yapılamayacağı için baskı da yapılamaz.
    monkeypatch.setattr("km_platform.printer.cups.shutil.which",
                        lambda name: "/usr/bin/lp" if name == "lp" else None)
    assert PrinterService.installed() is False


@pytest.mark.asyncio
async def test_baski_isinin_omru_baglanir(tmp_path, monkeypatch) -> None:
    """`job-cancel-after` gider — takılı iş sonsuza dek yeniden denenmesin.

    Uygulama tek iş gönderiyor. Tekrarın kaynağı `lp`den SONRASI olabilir:
    `ErrorPolicy=retry-job` olan kuyrukta cihazda hata alan iş kuyrukta kalır
    ve düzenli aralıklarla yeniden denenir. Sahada görülen "bir kez yazdır
    dedim, yarım saat boyunca bastı" tam olarak bu şekle uyuyor.
    """
    service = PrinterService(FakeConfig({}), _Log())
    pdf = tmp_path / "rapor.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list = []
    _yazici_kurulumu(service, monkeypatch, calls)

    await service.print_file(pdf, title="rapor")

    assert "job-cancel-after=180" in calls[0]


def test_is_omru_ayardan_kapatilabilir() -> None:
    service = PrinterService(FakeConfig({"job_lifetime_seconds": 0}), _Log())
    assert service._job_lifetime == 0


def test_is_omru_ayardan_degistirilebilir() -> None:
    service = PrinterService(FakeConfig({"job_lifetime_seconds": 600}), _Log())
    assert service._job_lifetime == 600
