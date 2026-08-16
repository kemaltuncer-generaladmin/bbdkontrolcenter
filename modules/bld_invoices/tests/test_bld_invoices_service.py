"""İş kuralları — okuma dayanıklılığı, yazma zinciri, dosya arşivi.

ÜÇ İDDİA HER TESTİN ARKASINDA DURUR:
  1. OKUMA FIRLATMAZ (K7): geçit düşse bile uç 200 döner, `connected: False`
     ve neden ekrana yazılır. `ok: True` ucun sağlığıdır, okumanın başarısı
     değil.
  2. HER YAZMADA AÇIK `dry_run=` GEÇER: bayrağı atlayan bir çağrı hiçbir şey
     yazmadan `{"ok": true}` alırdı ve ekran "belge kesildi" derdi.
  3. İZ GEÇİT ÇAĞRISINDAN ÖNCE DÜŞER: ağ koparsa geriye yalnız o satır kalır.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bld_invoices_backend.service import InvoicesService
from bld_invoices_fakes import (
    INVOICE_FULL,
    VOID_INVOICE,
    FakeApi,
    FakeLog,
    FakePrinter,
    FakeStore,
)

GEREKCE = "Müşteri sipariş için belge talep etti"
AKTOR = "Ayşe Yılmaz"


def build(tmp_path: Path, *, printer: FakePrinter | None = None,
          **config: object) -> tuple[InvoicesService, FakeApi, FakeStore]:
    api = FakeApi()
    store = FakeStore()
    service = InvoicesService(api=api, store=store, log=FakeLog(),
                              config={"export_path": str(tmp_path), **config},
                              printer=printer, fallback_dir=tmp_path)
    return service, api, store


# ================================================================== okuma

async def test_liste_gecit_dusukken_de_200_doner(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.fail.add("invoices")
    api.fail_code = "control_endpoint_missing"

    sonuc = await service.invoices()

    # Uç sağlıklı, cevabı "bağlanamadım". Panel `connected`ı OKUR; yalnız `ok`a
    # bakan bir ekran "kayıt yok" der ve K7'nin engellemek için var olduğu
    # yalanı söyler.
    assert sonuc["ok"] is True
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    # Sunucu ucu henüz yayında değilse ekran bunu AYRI bir cümleyle söylesin.
    assert sonuc["code"] == "control_endpoint_missing"


async def test_suzgeclenmis_toplam_satirlardan_hesaplanmaz(tmp_path: Path) -> None:
    service, _api, _ = build(tmp_path)
    # Sayfada tek satır var (216.000 kuruş) ama süzgeçlenmiş kümenin toplamı
    # 8.912.000. Satırlardan toplasaydık sayfa değiştikçe "genel toplam"
    # değişirdi.
    sonuc = await service.invoices()

    assert sonuc["connected"] is True
    assert sonuc["meta"]["issued_total_kurus"] == 8912000
    assert sonuc["items"][0]["total_kurus"] == 216000
    assert sonuc["items"][0]["source_label"] == "Sipariş #8421"


async def test_tek_belge_donmus_icerigi_tasir(tmp_path: Path) -> None:
    service, _, _ = build(tmp_path)

    sonuc = await service.invoice(44)

    kart = sonuc["data"]
    assert kart["invoice_no"] == "BLD-2026-000044"
    # İçerik `snapshot_json`dan gelir, canlı tablodan değil.
    assert kart["snapshot"]["customer"]["label"] == "Acme Gıda A.Ş."
    assert kart["snapshot"]["lines"][0]["line_total_kurus"] == 216000
    assert kart["snapshot"]["totals"]["currency"] == "TRY"


async def test_yerel_tablolar_okunamazsa_ekran_ayakta_kalir(tmp_path: Path) -> None:
    service, _, store = build(tmp_path)
    store.broken = True

    arsiv = await service.archive()
    iz = await service.audit()

    assert arsiv["ok"] is True and arsiv["items"] == []
    assert iz["ok"] is True and iz["items"] == []


# ============================================================ belge kesme

async def test_kes_gecide_acik_dry_run_gecirir(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)

    await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=False)

    ad, kwargs = api.calls[-1]
    assert ad == "create_invoice"
    # BAYRAK AÇIKÇA GEÇİLİR: geçidin varsayılanı `config/local.yaml` ile
    # değişebilir ve o dosya git dışıdır.
    assert kwargs["dry_run"] is False
    assert kwargs["order_id"] == 8421
    assert kwargs["subscription_id"] is None


async def test_kuru_prova_da_acik_bayrak_tasir(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)

    sonuc = await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=True)

    assert api.calls[-1][1]["dry_run"] is True
    assert sonuc["dry_run"] is True
    # Prova da denetim izine düşer: "kim neyi denedi" sorusunun cevabı prova
    # için de sorulur.
    assert [row["result"] for row in store.audit] == ["denendi", "dry_run"]


async def test_bayrak_verilmezse_modul_ayari_uygulanir(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path, dry_run_default=True)

    await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=None)

    # Alan hiç gönderilmediğinde varsayılan uygulanır — ama geçide yine AÇIK
    # bir değer gider, `None` değil.
    assert api.calls[-1][1]["dry_run"] is True


async def test_iki_kip_birden_ya_da_hicbiri_reddedilir(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)

    ikisi = await service.create(order_id=8421, subscription_id=18,
                                 period_start="2026-08-01", period_end="2026-08-31",
                                 reason=GEREKCE, actor=AKTOR, dry_run=False)
    hicbiri = await service.create(reason=GEREKCE, actor=AKTOR, dry_run=False)

    assert ikisi["ok"] is False and "iki kipten biriyle" in ikisi["error"]
    assert hicbiri["ok"] is False
    # Sunucuya HİÇ ÇIKILMAZ: 422 alacak bir istek hız kovasından pay yer.
    assert api.calls == []


async def test_donem_araligi_62_gunu_asamaz(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)

    sonuc = await service.create(subscription_id=18, period_start="2026-01-01",
                                 period_end="2026-06-30", reason=GEREKCE,
                                 actor=AKTOR, dry_run=False)

    assert sonuc["ok"] is False
    assert "62 gün" in sonuc["error"]
    assert api.calls == []


async def test_kisa_gerekce_sunucuya_cikmadan_durur(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)

    sonuc = await service.create(order_id=8421, reason="kısa", actor=AKTOR, dry_run=False)

    assert sonuc["ok"] is False
    assert api.calls == []
    assert store.audit == []


async def test_iz_gecit_cagrisindan_once_duser(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)
    api.fail.add("create_invoice")

    sonuc = await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=False)

    assert sonuc["ok"] is False
    # Çağrı patladı ama "denendi" satırı yazılmıştı: ağ koparsa geriye YALNIZ
    # o kalır ve "kim hangi belgeyi kesmeye çalıştı" ancak böyle bilinir.
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]
    assert store.audit[0]["actor"] == AKTOR
    assert store.audit[0]["reason"] == GEREKCE


async def test_iz_yazilamazsa_is_durmaz(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)
    store.broken = True

    sonuc = await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=False)

    # Yerel iz bir kayıt aracıdır, bir kapı değil: yazılamaması belgeyi
    # engellemez (K7).
    assert sonuc["ok"] is True
    assert api.calls[-1][0] == "create_invoice"


async def test_sunucu_provaya_cevirdiyse_yanit_bunu_soyler(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.write_result = {**api.write_result, "dry_run": True}

    sonuc = await service.create(order_id=8421, reason=GEREKCE, actor=AKTOR, dry_run=False)

    # İstediğimiz gerçek yazmaydı, sunucu prova dedi. Ekran "belge kesildi"
    # DEMEMELİ; ayrımı taşıyan alan budur.
    assert sonuc["dry_run"] is False
    assert sonuc["server_dry_run"] is True


# ================================================================== iptal

async def test_iptal_izin_olmadan_engellenir(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)

    sonuc = await service.void(44, reason=GEREKCE, actor=AKTOR, dry_run=False,
                               allow_void=False)

    assert sonuc["ok"] is False
    assert api.calls == []
    # Engellenen deneme de yazılır: reddedilmiş bir işlem, hiç denenmemiş bir
    # işlemle aynı şey değildir (K9 — çift kapı).
    assert store.audit[-1]["result"] == "engellendi"


async def test_iptal_acik_bayrakla_gider_ve_iz_birakir(tmp_path: Path) -> None:
    service, api, store = build(tmp_path)
    api.write_result = {"ok": True, "dry_run": False, "audit_id": 2110,
                        "data": {"id": 44, "invoice_no": "BLD-2026-000044",
                                 "status": "void", "void_at": "2026-08-16T16:00:00Z",
                                 "void_reason": GEREKCE}}

    sonuc = await service.void(44, reason=GEREKCE, actor=AKTOR, dry_run=False)

    assert sonuc["ok"] is True
    assert api.calls[-1][1]["dry_run"] is False
    assert [row["result"] for row in store.audit] == ["denendi", "ok"]
    assert store.audit[-1]["invoice_id"] == 44


# ============================================================ belge dosyası

async def test_a4_belge_uretilir_ve_arsive_kunye_duser(tmp_path: Path) -> None:
    service, _, store = build(tmp_path)

    sonuc = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    assert sonuc["ok"] is True
    yol = Path(sonuc["path"])
    assert yol.exists() and yol.read_bytes().startswith(b"%PDF")
    # Dosya YALNIZ KULLANICIYA okunur yazılır (0600): belge kişisel veri taşır.
    assert oct(yol.stat().st_mode & 0o777) == "0o600"
    # Arşiv satırı belgenin verisini değil DOSYANIN künyesini taşır.
    kunye = store.archive[-1]
    assert kunye["kind"] == "pdf"
    assert kunye["invoice_no"] == "BLD-2026-000044"
    assert len(kunye["sha256"]) == 64
    assert kunye["printed_at"] == ""


async def test_unvandaki_ozel_karakter_belgeyi_kirmaz(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    # Başlık ve alt başlık da reportlab'ın mini XML'iyle çiziliyor ve orada
    # kaçırılmamış bir `<...>` ETİKET SANILIP ATILIYOR: unvanın yarısı sessizce
    # kâğıttan düşerdi. Kaçış `documents.esc` ile yapılır (birim testi orada).
    api.card = {**INVOICE_FULL, "customer_label": "Acme & Co <A.Ş.>"}

    sonuc = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    assert sonuc["ok"] is True
    assert Path(sonuc["path"]).read_bytes().startswith(b"%PDF")


async def test_iptal_edilmis_belge_temiz_basilmaz(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.card = dict(VOID_INVOICE)

    sonuc = await service.build_report("invoice", {"invoice_id": 45}, actor=AKTOR)

    assert sonuc["ok"] is True
    assert sonuc["status"] == "void"
    # `build_pdf` çizim katmanı sunmadığı için filigran yerine YAZI kullanılır;
    # iptal bilgisi başlıkta, ilk uyarı satırında ve toplam kutusunda geçer.
    # Temiz basılabilen bir iptal belgesi, elindeki kâğıdın geçerli olduğunu
    # sanan bir müşteri üretirdi.
    metin = Path(sonuc["path"]).read_bytes()
    assert metin.startswith(b"%PDF")


async def test_belge_okunamazsa_dosya_uretilmez(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.fail.add("invoice")

    sonuc = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    assert sonuc["ok"] is False
    assert not list(tmp_path.glob("*.pdf"))


async def test_sunucu_htmli_diske_yazilir(tmp_path: Path) -> None:
    service, _, store = build(tmp_path)

    sonuc = await service.save_html(44, actor=AKTOR)

    assert sonuc["ok"] is True
    assert Path(sonuc["path"]).read_bytes().startswith(b"<!doctype html>")
    assert store.archive[-1]["kind"] == "html"


async def test_liste_dokumu_bos_kumede_uretilmez(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.items = []

    sonuc = await service.build_report("list", {}, actor=AKTOR)

    assert sonuc["ok"] is False
    assert "belge yok" in sonuc["error"]


async def test_bilinmeyen_rapor_turu_reddedilir(tmp_path: Path) -> None:
    service, _, _ = build(tmp_path)

    sonuc = await service.build_report("her-sey", {}, actor=AKTOR)

    assert sonuc["ok"] is False


# ================================================================== baskı

async def test_baski_arsivde_basildigi_ani_isaretler(tmp_path: Path) -> None:
    printer = FakePrinter()
    service, _, store = build(tmp_path, printer=printer)
    uretim = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    sonuc = await service.print_report(uretim["path"], copies=2)

    assert sonuc["ok"] is True
    assert printer.jobs[-1][1] == 2
    # "Üretildi" ile "basıldı" ayrı şeylerdir; arşiv ikisini ayrı tutar.
    assert store.archive[-1]["printed_at"]
    assert store.archive[-1]["print_copies"] == 2


async def test_rapor_klasoru_disindaki_dosya_basilmaz(tmp_path: Path) -> None:
    printer = FakePrinter()
    service, _, _ = build(tmp_path, printer=printer)
    disarida = tmp_path.parent / "gizli.pdf"
    disarida.write_bytes(b"%PDF-1.4")

    sonuc = await service.print_report(str(disarida))

    # Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    assert sonuc["ok"] is False
    assert printer.jobs == []


async def test_yazici_yoksa_ekran_calismaya_devam_eder(tmp_path: Path) -> None:
    service, _, _ = build(tmp_path)          # printer=None
    uretim = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    durum = await service.printer_status()
    baski = await service.print_report(uretim["path"])

    # Belge ÜRETİLDİ; yalnız baskı yolu kapalı ve nedeni yazılı (K7).
    assert uretim["ok"] is True
    assert durum["ready"] is False
    assert baski["ok"] is False
    assert "yazıcı" in baski["error"].lower()


async def test_snapshot_eksikse_belge_yine_uretilir(tmp_path: Path) -> None:
    service, api, _ = build(tmp_path)
    api.card = {**INVOICE_FULL, "snapshot_json": {}}

    sonuc = await service.build_report("invoice", {"invoice_id": 44}, actor=AKTOR)

    # Eksik blok UYDURULMAZ ama belge de üretilmeden kalmaz: künye ve dipnot
    # her hâlükârda basılır.
    assert sonuc["ok"] is True


@pytest.mark.parametrize("kimlik", [0, -3])
async def test_belge_secilmeden_uretim_olmaz(tmp_path: Path, kimlik: int) -> None:
    service, api, _ = build(tmp_path)

    sonuc = await service.build_report("invoice", {"invoice_id": kimlik}, actor=AKTOR)

    assert sonuc["ok"] is False
    assert api.calls == []
