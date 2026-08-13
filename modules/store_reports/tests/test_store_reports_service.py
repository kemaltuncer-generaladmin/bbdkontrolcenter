"""Raporlar servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir.

PDF üretimi GERÇEKTEN çalıştırılır ama geçici bir klasöre yazılır: rapor
paketinin bölüm sırası ve yol güvenliği ancak dosya üretilerek sınanabilir.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from store_reports_backend.service import ReportsService
from store_reports_fakes import FakeApi, FakeLog, FakeStore, line, order

GUN = "2026-08-13"
ARALIK = {"start": "2026-08-01", "end": "2026-08-31"}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[ReportsService, FakeApi, FakeStore, Path]:
    api = api or FakeApi([order(1, GUN, "1200.00")])
    store = store or FakeStore()
    folder = Path(tempfile.mkdtemp(prefix="km-rapor-testi-"))
    service = ReportsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "export_path": str(folder), **config},
        fallback_dir=folder,
    )
    return service, api, store, folder


# ============================================================ K7 — ayakta kalma

async def test_siparis_ucu_patlarsa_yaprak_duser_agac_durur() -> None:
    service, api, _, _ = _service()
    api.fail.add("orders")
    result = await service.run("sales_summary", ARALIK)
    assert result["ok"] is False
    assert "patladı" in result["error"]
    # Ağacın kendisi hâlâ çiziliyor: ekran boş kalmıyor.
    catalog = await service.catalog()
    assert catalog["ok"] is True
    assert len(catalog["branches"]) == 7


async def test_kargo_ucu_patlasa_bile_satis_raporu_calisir() -> None:
    service, api, _, _ = _service()
    api.fail.add("bbd_shipments")
    kargo = await service.run("shipping_carrier", ARALIK)
    satis = await service.run("sales_summary", ARALIK)
    assert kargo["ok"] is False
    assert satis["ok"] is True


async def test_pakette_patlayan_bolum_notla_yerinde_durur() -> None:
    service, api, _, _ = _service()
    api.fail.add("bbd_shipments")
    result = await service.build_report("package", {
        **ARALIK, "leaves": ["sales_summary", "shipping_carrier"], "title": "Haftalık"})
    assert result["ok"] is True
    assert result["failed"] == ["shipping_carrier"]
    assert Path(result["path"]).exists()


async def test_ucu_olmayan_yaprak_calistirilamaz_ama_anlatir() -> None:
    service, _, _, _ = _service()
    result = await service.run("sales_abandoned", ARALIK)
    assert result["ok"] is False
    assert result["pending"] is True
    assert "abandoned_carts" in result["error"]


# ================================================= veri bir kez çekilir

async def test_paket_veri_kumesini_tekrar_tekrar_cekmez() -> None:
    service, api, _, _ = _service()
    await service.build_report("package", {
        **ARALIK, "leaves": ["sales_summary", "sales_channel", "sales_payment",
                             "customer_city"]})
    assert api.used("orders") == 1


async def test_tarih_suzgeci_uygulanmadiysa_yerelde_yeniden_uygulanir() -> None:
    """Laravel tanımadığı sorgu parametresini sessizce yok sayar."""
    api = FakeApi([order(1, GUN, "100.00"), order(2, "2025-01-05", "999.00")])
    service, _, _, _ = _service(api)
    result = await service.run("sales_summary", ARALIK)
    labels = {item["label"]: item["value"] for item in result["kpis"]}
    assert labels["Sipariş"] == "1"


# ================================================== kalem tavanı ve notu

async def test_kalem_liste_ucunda_yoksa_siparis_tek_tek_okunur() -> None:
    api = FakeApi([order(1, GUN, "100.00")])
    api.details = {1: {**order(1, GUN, "100.00"),
                       "items": [line(5, "Kalem", 2, "100.00")]}}
    service, _, _, _ = _service(api)
    result = await service.run("product_top", ARALIK)
    assert result["ok"] is True
    assert api.used("order") == 1
    assert result["table"]["rows"][0][1] == "SKU-5"


async def test_kalem_tavani_asilirsa_rapor_eksikligi_yazar() -> None:
    api = FakeApi([order(no, GUN, "100.00") for no in range(1, 6)])
    api.details = {no: {**order(no, GUN, "100.00"),
                        "items": [line(no, "Ürün", 1, "100.00")]} for no in range(1, 6)}
    service, _, _, _ = _service(api, detail_scan_limit=2)
    result = await service.run("product_top", ARALIK)
    assert result["ok"] is True
    assert any("tek tek okundu" in note for note in result["notes"])


async def test_kalem_okuma_kapaliyken_sessiz_sifir_gostermez() -> None:
    api = FakeApi([order(1, GUN, "100.00")])
    service, _, _, _ = _service(api, detail_scan_limit=0)
    result = await service.run("product_top", ARALIK)
    assert any("eksiktir" in note for note in result["notes"])


# ================================================================= rapor

async def test_tek_yaprak_pdf_uretir_ve_iz_birakir() -> None:
    service, _, store, folder = _service()
    result = await service.build_report("sales_summary", ARALIK)
    assert result["ok"] is True
    assert Path(result["path"]).parent == folder.resolve() or Path(result["path"]).exists()
    assert store.runs[-1]["ok"] == 1


async def test_paket_kapak_ve_icindekiler_ile_baslar() -> None:
    service, _, _, _ = _service()
    params = service.params(ARALIK)
    sections = service._cover("Haftalık paket", ["sales_summary", "sales_channel"], params)
    assert sections[0]["kind"] == "tiles"
    assert sections[1]["title"] == "İçindekiler"
    assert sections[-1]["kind"] == "break"


async def test_paket_yaprak_sinirini_asamaz() -> None:
    service, _, _, _ = _service(package_leaf_limit=2)
    result = await service.build_report("package", {
        **ARALIK, "leaves": ["sales_summary", "sales_channel", "sales_payment"]})
    assert result["ok"] is False
    assert "en çok 2" in result["error"]


async def test_csv_disa_aktarma_rapor_klasorune_yazar() -> None:
    service, _, _, folder = _service()
    result = await service.export_csv("sales_summary", ARALIK)
    assert result["ok"] is True
    assert Path(result["path"]).exists()
    assert Path(result["path"]).parent == folder.resolve()


# ====================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class Printer:
        async def print_file(self, path: Path, *, title: str = "",
                             copies: int = 1) -> dict[str, Any]:
            return {"printer": "test"}

        async def status(self) -> dict[str, Any]:
            return {"ready": True}

    service, _, _, _ = _service()
    service._printer = Printer()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]


async def test_kendi_klasorundeki_rapor_basilir() -> None:
    class Printer:
        def __init__(self) -> None:
            self.sent: list[Path] = []

        async def print_file(self, path: Path, *, title: str = "",
                             copies: int = 1) -> dict[str, Any]:
            self.sent.append(path)
            return {"printer": "test"}

    service, _, _, _ = _service()
    printer = Printer()
    service._printer = printer
    produced = await service.build_report("sales_summary", ARALIK)
    result = await service.print_report(produced["path"])
    assert result["ok"] is True
    assert printer.sent


# ============================================ kaydedilmiş / zamanlanmış

async def test_kaydedilmis_rapor_yapraklariyla_saklanir() -> None:
    service, _, store, _ = _service()
    result = await service.save(name="Haftalık satış", leaves=["sales_summary", "yok"],
                                params=ARALIK, actor="Ali")
    assert result["ok"] is True
    assert result["leaves"] == ["sales_summary"]      # tanınmayan yaprak düşer
    listed = await service.saved()
    assert listed["items"][0]["labels"] == ["Satış · Genel özet"]
    assert len(store.saved) == 1


async def test_kaydedilmis_rapor_silinmez_arsivlenir() -> None:
    service, _, store, _ = _service()
    await service.save(name="Aylık", leaves=["sales_summary"], params=ARALIK, actor="Ali")
    kisa = await service.archive_saved(store.saved[0]["id"], reason="kısa", actor="Ali")
    assert kisa["ok"] is False                       # gerekçe backend'de de doğrulanır
    ok = await service.archive_saved(store.saved[0]["id"],
                                     reason="Artık kullanılmıyor, yerine yenisi var",
                                     actor="Ali")
    assert ok["ok"] is True
    assert store.saved[0]["active"] == 0             # SATIR DURUYOR
    assert (await service.saved())["items"] == []


async def test_zamanlanmis_rapor_saat_bicimini_dogrular() -> None:
    service, _, _, _ = _service()
    bad = await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                                 period="weekly", weekday=0, day_of_month=1,
                                 at_time="25:99", actor="Ali")
    assert bad["ok"] is False
    good = await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                                  period="weekly", weekday=0, day_of_month=1,
                                  at_time="07:00", actor="Ali")
    assert good["ok"] is True


async def test_zamanlanmis_rapor_ayni_gun_iki_kez_uretilmez() -> None:
    service, _, store, _ = _service()
    await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    pazartesi = datetime(2026, 8, 17, 9, 0).astimezone()      # pazartesi 09:00
    first = await service.run_due(when=pazartesi)
    second = await service.run_due(when=pazartesi)
    assert first["produced"] == 1
    assert second["produced"] == 0
    assert store.schedules[0]["last_result"].startswith("Üretildi")


async def test_zamani_gelmeyen_tanim_calismaz() -> None:
    service, _, _, _ = _service()
    await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    sali = datetime(2026, 8, 18, 9, 0).astimezone()
    result = await service.run_due(when=sali)
    assert result["produced"] == 0
    assert result["skipped"] == 1


# ============================================================== parametre

async def test_ters_aralik_cevrilir_bos_rapor_uretilmez() -> None:
    service, _, _, _ = _service()
    params = service.params({"start": "2026-08-31", "end": "2026-08-01"})
    assert params["start"] == "2026-08-01"
    assert params["end"] == "2026-08-31"


async def test_karsilastirma_penceresi_cekim_araligini_genisletir() -> None:
    service, _, _, _ = _service()
    params = service.params({**ARALIK, "compare": "previous"})
    start, end = ReportsService._window(params)
    assert start == params["prevStart"]
    assert end == params["end"]


async def test_bilinmeyen_kirilim_gune_duser() -> None:
    service, _, _, _ = _service()
    assert service.params({"granularity": "saat"})["granularity"] == "day"


async def test_yeni_musteri_olcusu_donem_degil_tum_gecmis_uzerinden_okunur() -> None:
    """Üç yıllık müşteriyi "yeni" göstermemek için geçmişin tamamı çekilir."""
    api = FakeApi([order(1, "2024-05-05", "100.00", customer_id=7),
                   order(2, GUN, "100.00", customer_id=7)])
    service, _, _, _ = _service(api)
    result = await service.run("customer_new_returning", ARALIK)
    labels = {item["label"]: item["value"] for item in result["kpis"]}
    assert labels["Yeni müşteri siparişi"] == "0"
    assert labels["Tekrar eden sipariş"] == "1"


async def test_kayip_musteri_esigi_ayardan_gelir_ve_notta_yazar() -> None:
    api = FakeApi([order(1, "2024-01-01", "100.00", customer_id=7)])
    service, _, _, _ = _service(api, churn_days=90)
    result = await service.run("customer_churn", {"start": "2026-08-01", "end": "2026-08-31"})
    assert result["table"]["rows"][0][0].startswith("Ali")
    assert any("90 gün" in note for note in result["notes"])


# ================================================== kanal süzgeci — sessiz boşaltıcı

async def test_kanal_kodu_siparis_ucuna_GONDERILMEZ() -> None:
    """CANLIDA: `/orders?channel=default` → 0 kayıt, hata yok.

    Kanal KODUNU sipariş ucuna eklemek 35 yaprağın 25'ini sessizce boşaltırdı.
    Kimlik ayarlanmadıysa süzgeç HİÇ gönderilmez.
    """
    service, api, _, _ = _service()
    await service.run("sales_summary", ARALIK)
    sent = [args[0] for name, args, _ in api.calls if name == "orders"]
    assert sent and all("channel" not in (filters or {}) for filters in sent)


async def test_kanal_kimligi_ayarlandiysa_kimlik_gider_kod_degil() -> None:
    service, api, _, _ = _service(channel_id=1)
    await service.run("sales_summary", ARALIK)
    sent = [args[0] for name, args, _ in api.calls if name == "orders"]
    assert sent[0]["channel"] == 1                    # "default" DEĞİL


async def test_urun_ucu_kanal_KODUNU_almaya_devam_eder() -> None:
    """Ürün ucu tersini istiyor: `channel=default` → 1421 ürün, `channel=1` → 0."""
    api = FakeApi([order(1, GUN, "100.00")])
    service, _, _, _ = _service(api, channel_id=1)
    await service.run("product_dead", ARALIK)
    sent = [args[0] for name, args, _ in api.calls if name == "products"]
    assert sent[0]["channel"] == "default"


# ================================================ müşteri grubu — iki listeyi birleştirir

async def test_musteri_grubu_musteri_listesinden_cozulur() -> None:
    """Sipariş listesi grubu HİÇ taşımıyor; eşleşmezse her satır "Grupsuz" olurdu."""
    api = FakeApi([order(1, GUN, "100.00", customer_id=7, customer_group=None)])
    api.customer_list = [{"id": 7, "name": "Ali Veli", "email": "musteri1@ornek.com",
                          "group": {"id": 2, "code": "general", "name": "Bayi"}}]
    service, _, _, _ = _service(api)
    result = await service.run("customer_group", ARALIK)
    assert result["ok"] is True
    assert result["table"]["rows"][0][0] == "Bayi"


async def test_grubu_cozulemeyen_siparis_sessizce_grupsuz_sayilmaz() -> None:
    api = FakeApi([order(1, GUN, "100.00", customer_id=99, customer_group=None)])
    api.customer_list = []
    service, _, _, _ = _service(api)
    result = await service.run("customer_group", ARALIK)
    assert any("çözülemedi" in note for note in result["notes"])


async def test_musteri_ucu_patlasa_bile_grup_raporu_ayakta_kalir() -> None:
    api = FakeApi([order(1, GUN, "100.00")])
    api.fail.add("customers")
    service, _, _, _ = _service(api)
    result = await service.run("customer_group", ARALIK)
    assert result["ok"] is False                      # küme bazlı hata, çöküş değil
    assert "patladı" in result["error"]


# ============================================ referans listeler — açılırlar boş kalmasın

async def test_kanal_acilirini_KOD_degil_AD_doldurur() -> None:
    """Süzgeç yerelde `channelName`e karşı uygulanıyor; kod eşleşmez, rapor boşalır."""
    service, _, _, _ = _service()
    reference = await service.reference()
    assert reference["channels"] == ["Varsayılan"]


async def test_kategori_acilirinda_kok_degil_gercek_kategoriler_durur() -> None:
    service, _, _, _ = _service()
    reference = await service.reference()
    assert reference["categories"] == ["Deneme", "Kitap"]


# ======================================================== K7 — bozuk kayıt turu düşürmez

async def test_bozuk_json_kaydedilmis_rapor_listesini_dusurmez() -> None:
    service, _, store, _ = _service()
    await service.save(name="Haftalık", leaves=["sales_summary"], params=ARALIK, actor="Ali")
    store.saved[0]["leaves"] = "{bozuk"
    listed = await service.saved()
    assert listed["ok"] is True
    assert listed["items"][0]["leaves"] == []


async def test_damga_yazilamazsa_zamanlama_turu_devam_eder() -> None:
    service, _, store, _ = _service()
    await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")

    original = store.execute

    async def kirik(sql: str, params: tuple[Any, ...] = ()) -> None:
        if "last_run_at = ?" in " ".join(sql.split()):
            raise RuntimeError("disk dolu")
        await original(sql, params)

    store.execute = kirik                              # type: ignore[method-assign]
    pazartesi = datetime(2026, 8, 17, 9, 0).astimezone()
    result = await service.run_due(when=pazartesi)
    assert result["ok"] is True                        # tur patlamadı
    assert result["produced"] == 1


# ======================================== K7 — bozuk satır sekmeyi kapatmaz

async def test_yanlis_TURDE_json_kaydedilmis_sekmesini_dusurmez() -> None:
    """Ayrıştırılabilir ama yanlış türdeki değer eski korumadan sızıyordu.

    `"{bozuk"` yakalanıyordu; `"5"` yakalanmıyordu — `json.loads` onu int
    yapıyor, `_saved_row` üzerinde dolaşınca `TypeError` atıyor ve istisna
    `saved()` içindeki liste kurgusunda (try bloğunun DIŞINDA) patlayıp
    sekmenin tamamını 500'e düşürüyordu.
    """
    service, _, store, _ = _service()
    await service.save(name="Haftalık", leaves=["sales_summary"], params=ARALIK, actor="Ali")
    store.saved[0]["leaves"] = "5"
    store.saved[0]["params"] = '"metin"'
    listed = await service.saved()
    assert listed["ok"] is True
    assert listed["items"][0]["leaves"] == []
    assert listed["items"][0]["params"] == {}


async def test_yanlis_turde_json_zamanlanmis_sekmesini_de_dusurmez() -> None:
    service, _, store, _ = _service()
    await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    store.schedules[0]["leaves"] = "12"
    listed = await service.schedules()
    assert listed["ok"] is True
    assert listed["items"][0]["leaves"] == []
    assert listed["items"][0]["labels"] == []


# ============================ K7 — bozuk saat damgası turu iptal etmiyordu

async def test_bozuk_saat_damgasi_zamanlama_turunu_iptal_etmez() -> None:
    """`"7".split(":")` tek parça verir; eski kod ikiye çözmeye çalışıp
    `ValueError` atıyor, istisna `run_scheduled`e kadar çıkıyor ve o turdaki
    BÜTÜN zamanlanmış raporlar üretilmiyordu."""
    service, _, store, _ = _service()
    await service.schedule(name="Bozuk", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    await service.schedule(name="Sağlam", leaves=["sales_channel"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    store.schedules[0]["at_time"] = "7"          # elle bozulmuş satır
    pazartesi = datetime(2026, 8, 17, 9, 0).astimezone()
    result = await service.run_due(when=pazartesi)
    assert result["ok"] is True
    assert result["produced"] == 2               # bozuk satır 07:00'e düşer, ikisi de çıkar


async def test_zamanlanmis_kosucu_hata_firlatmaz() -> None:
    """`run_scheduled` sözü: istisna değil, sözlük döner."""
    from store_reports_backend import module as module_entry
    from store_reports_backend import tasks

    service, _, store, _ = _service()
    await service.schedule(name="Haftalık", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    store.schedules[0]["at_time"] = ""           # damga tamamen boş
    # `register(ctx)` sırasında saklanan canlı servisin yerine geçilir;
    # koşucu bağlamsız çağrıldığında tek tutamağı budur.
    module_entry._LIVE = service                 # type: ignore[assignment]
    try:
        result = await tasks.run_scheduled()
    finally:
        module_entry._LIVE = None
    assert result["ok"] is True


# ==================================================== üretim izi — üreten

async def test_uretim_izi_kimin_urettigini_yazar() -> None:
    """`actor` sütunu göçte vardı ama HİÇ doldurulmuyordu: geçmiş sekmesi
    raporu kimin aldığını söyleyemiyordu."""
    service, _, store, _ = _service()
    await service.build_report("sales_summary", ARALIK, actor="Ayşe Şahin")
    assert store.runs[-1]["actor"] == "Ayşe Şahin"


async def test_csv_izi_de_imzalanir() -> None:
    service, _, store, _ = _service()
    await service.export_csv("sales_summary", ARALIK, actor="Ayşe Şahin")
    assert store.runs[-1]["actor"] == "Ayşe Şahin"


async def test_zamanlanmis_uretim_izi_zamanlanmis_olarak_isaretlenir() -> None:
    """Kimse düğmeye basmadı; iz bunu söylemezse elle alınan raporla karışır."""
    service, _, store, _ = _service()
    await service.schedule(name="Haftalık satış", leaves=["sales_summary"], params=ARALIK,
                           period="weekly", weekday=0, day_of_month=1, at_time="07:00",
                           actor="Ali")
    await service.run_due(when=datetime(2026, 8, 17, 9, 0).astimezone())
    assert store.runs[-1]["actor"] == "Zamanlanmış: Haftalık satış"


async def test_gecmis_satiri_ham_anahtar_degil_turkce_etiket_tasir() -> None:
    """Panel `sales_summary` yazıyordu; ekran metinleri Türkçedir."""
    service, _, _, _ = _service()
    await service.build_report("sales_summary", ARALIK, actor="Ali")
    listed = await service.runs()
    assert listed["items"][0]["labels"] == ["Satış · Genel özet"]
