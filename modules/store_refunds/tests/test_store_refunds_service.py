"""İadeler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_refunds_backend import calc
from store_refunds_backend.service import RefundsService
from store_refunds_fakes import (
    LIVE_ORDER,
    LIVE_REFUND,
    LIVE_RETURN_REQUEST,
    LIVE_SALES_STATS,
    ORDER,
    FakeApi,
    FakeLog,
    FakePrinter,
    FakeStore,
)

GEREKCE = "müşteri ayıplı ürün bildirdi, kargo geri alındı"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             printer: Any = None, **config: Any) -> tuple[RefundsService, FakeApi, FakeStore]:
    api = api or FakeApi({12: json.loads(json.dumps(ORDER))})
    store = store or FakeStore()
    service = RefundsService(
        api=api, store=store, log=FakeLog(), printer=printer,
        config={"channel": "default", "locale": "tr", "default_range_days": 90, **config},
        fallback_dir=Path("/tmp/km-test-iade-raporlari"),
    )
    return service, api, store


def _refund_payload(order_id: int = 12, total: str = "110.00") -> dict[str, Any]:
    return {"items": [{"id": 1, "increment_id": "K-1", "order_id": order_id,
                       "created_at": "2026-08-10T09:00:00", "grand_total": total,
                       "order": {"id": order_id, "increment_id": "S-1001"},
                       "items": [{"name": "Matematik"}]}],
            "meta": {"total": 1, "lastPage": 1}}


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("refunds")
    api.fail.add("bbd_return_requests")
    result = await service.refunds()
    assert result["ok"] is True            # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert len(result["warnings"]) == 2


async def test_bbd_ucu_yoksa_talepler_kapali_kredi_notlari_gorunur() -> None:
    # `/api/admin/bbd/*` bugün YAYINDA (ölçüm 2026-08-16) — bu satır bir dönem
    # "hâlâ yazılıyor" diyordu. Test yine de DURUYOR ve durmalı: uç geri
    # çekilirse ekran düşmemeli, o bölümü KAPALI gösterip gerisini çizmeli (K7).
    # Sınanmayan bir savunma dalı, olmayan savunmadır.
    service, api, _ = _service()
    api.refunds_payload = _refund_payload()
    api.absent.add("bbd_return_requests")
    result = await service.refunds()
    assert result["connected"] is True
    assert len(result["items"]) == 1
    assert result["sources"]["requests"]["available"] is False
    assert result["sources"]["requests"]["missing"] is True
    assert "yayına girince" in result["sources"]["requests"]["error"]


async def test_cekmece_parcasi_patlarsa_gerisi_yine_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_payment_attempts")
    result = await service.order_card(12)
    assert result["ok"] is True
    assert result["order"]["orderNumber"] == "S-1001"
    assert result["payments"]["available"] is False
    assert any("POS denemeleri" in item for item in result["warnings"])


async def test_satis_okunamazsa_iade_orani_bos_kalir_ve_nedeni_yazilir() -> None:
    service, api, _ = _service()
    api.refunds_payload = _refund_payload()
    api.fail.add("dashboard_stats")
    result = await service.refunds()
    assert result["summary"]["refundRate"] is None
    assert "uydurulmaz" in result["summary"]["rateNote"]


# ================================================= pencere ve tarama sınırı

async def test_tarih_penceresi_verilmezse_varsayilan_gun_sayisi_kullanilir() -> None:
    service, api, _ = _service(default_range_days=30)
    await service.refunds(date_to="2026-08-31")
    filters = api.args("refunds")[0][0]
    assert filters["date_from"] == "2026-08-01"
    assert filters["date_to"] == "2026-08-31"


async def test_talepler_sayfa_sayfa_toplanir() -> None:
    service, api, _ = _service()
    api.requests_payload = {"items": [{"id": 5, "order_id": 99, "status": "requested",
                                       "created_at": "2026-08-09T09:00:00"}],
                            "meta": {"lastPage": 3}}
    await service.refunds()
    assert [call["page"] for call in api.used("bbd_return_requests")] == [1, 2, 3]


async def test_talep_sayfasi_ortada_patlarsa_eldeki_liste_gosterilir() -> None:
    service, api, _ = _service()

    calls = {"n": 0}
    original = api.bbd_return_requests

    async def flaky(filters: Any = None, *, page: int = 1,
                    per_page: int | None = None) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] > 1:
            api.fail.add("bbd_return_requests")
        return await original(filters, page=page, per_page=per_page)

    api.bbd_return_requests = flaky  # type: ignore[method-assign]
    api.requests_payload = {"items": [{"id": 5, "order_id": 99, "status": "requested",
                                       "created_at": "2026-08-09T09:00:00"}],
                            "meta": {"lastPage": 4}}
    result = await service.refunds()
    assert len(result["items"]) == 1
    assert result["sources"]["requests"]["available"] is True
    assert result["sources"]["requests"]["error"] != ""
    # Eksik liste SESSİZ kalmaz: "bugün açık talep yok" gibi görünmemeli.
    assert any("eksik alındı" in item for item in result["warnings"])


# ================================================================== hesap

async def test_hesap_satir_satir_doner_ve_jetonla_saklanir() -> None:
    service, api, store = _service()
    api.preview_payload = {"grand_total": "110.00"}
    result = await service.calculate(12, quantities={"101": 1})
    assert result["ok"] is True
    assert result["calc"]["total"] == 11_000
    assert result["store"]["matches"] is True
    saved = store.calc[result["token"]]
    assert saved["total"] == 11_000
    assert json.loads(saved["body"])["items"] == {"101": 1}


async def test_magaza_onizlemesi_alinamazsa_hesap_yine_gosterilir() -> None:
    service, api, _ = _service()
    api.fail.add("refund_preview")
    result = await service.calculate(12, quantities={"101": 1})
    assert result["ok"] is True
    assert result["store"]["matches"] is None
    assert "alınamadı" in result["store"]["message"]


async def test_magazanin_farkli_hesabi_ekranda_soylenir() -> None:
    service, api, _ = _service()
    api.preview_payload = {"grand_total": "115.00"}
    result = await service.calculate(12, quantities={"101": 1})
    assert result["store"]["matches"] is False
    assert result["store"]["diff"] == 500


async def test_adet_secilmeden_hesap_alinamaz() -> None:
    service, _, store = _service()
    result = await service.calculate(12, quantities={})
    assert result["ok"] is False
    assert store.calc == {}


async def test_kargo_iadesi_govdeye_secildigi_gibi_gider() -> None:
    service, _, store = _service()
    result = await service.calculate(12, quantities={"101": 1}, refund_shipping=True)
    body = json.loads(store.calc[result["token"]]["body"])
    assert body["adjustments"]["shipping"] == "48.00"
    assert result["calc"]["total"] == 15_800


# ============================================================= onay kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    result = await service.approve(token=preview["token"], reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("create_refund") == []


async def test_jeton_yoksa_onay_reddedilir() -> None:
    # Tutar görülmeden para çıkmaz.
    service, api, _ = _service()
    result = await service.approve(token="olmayan-jeton", reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert "Hesap bulunamadı" in result["error"]
    assert api.used("create_refund") == []


async def test_ayni_jeton_ikinci_kez_kullanilamaz() -> None:
    service, api, _ = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    first = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali",
                                  dry_run=False)
    assert first["ok"] is True
    second = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali",
                                   dry_run=False)
    assert second["ok"] is False
    assert len(api.used("create_refund")) == 1


async def test_onay_hesabi_yeniden_hesaplamaz_jetondaki_govdeyi_gonderir() -> None:
    # Ekranda ne onaylandıysa o gider: aradan geçen sürede sipariş değişse bile
    # kullanıcının görmediği bir tutar mağazaya yazılmaz.
    service, api, _ = _service()
    preview = await service.calculate(12, quantities={"101": 2})
    api.orders_by_id[12]["items"][0]["price"] = "999.00"
    await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali", dry_run=False)
    sent = api.used("create_refund")[0]
    assert sent["items"] == {"101": 2}
    assert sent["adjustments"]["shipping"] == "0.00"
    assert sent["reason"] == GEREKCE


async def test_kuru_prova_varsayilan_acik() -> None:
    service, api, _ = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    result = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali")
    assert api.used("create_refund")[0]["dry_run"] is True
    assert result["dryRun"] is True


async def test_onay_denetim_izine_gerekceyle_yazilir() -> None:
    service, _, store = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    await service.approve(token=preview["token"], reason=GEREKCE, actor="Ayşe Yılmaz",
                          dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "create_refund"]
    assert [row["result"] for row in kayitlar] == ["denendi", "ok"]
    assert kayitlar[0]["reason"] == GEREKCE
    assert kayitlar[0]["actor"] == "Ayşe Yılmaz"


async def test_magaza_yazmayi_reddederse_hata_ize_duser() -> None:
    service, api, store = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    api.fail.add("create_refund")
    result = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali",
                                   dry_run=False)
    assert result["ok"] is False
    assert any(row["result"] == "hata" for row in store.audit)
    # Jeton TÜKETİLMEZ: iş yapılmadı, kullanıcı yeniden deneyebilmeli.
    assert store.calc[preview["token"]]["status"] == "preview"


# =============================================================== POS iadesi

async def test_pos_iadesi_kredi_notundan_ayri_bir_adimdir() -> None:
    service, api, _ = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    approved = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali",
                                     dry_run=False)
    assert "POS iadesini" in approved["notice"]
    assert api.used("bbd_refund_payment") == []

    result = await service.pos_refund(attempt_id=7, amount=11_000, order_id=12, reason=GEREKCE,
                                      actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_refund_payment")[0]["amount"] == 11_000


async def test_pos_iadesinde_sifir_tutar_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.pos_refund(attempt_id=7, amount=0, reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_refund_payment") == []


async def test_pos_ucu_yoksa_neden_kapali_oldugu_soylenir() -> None:
    service, api, _ = _service()
    api.absent.add("bbd_refund_payment")
    result = await service.pos_refund(attempt_id=7, amount=100, reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert result["missing"] is True
    assert "yayına girince" in result["error"]


# ================================================================== süreç

async def test_iade_edildi_durumu_surec_ucundan_yazilamaz() -> None:
    # Para gerçekten geri verilmeden "iade edildi" yazmak, mutabakatta
    # kapatılamayan bir kayıt bırakır.
    service, api, _ = _service()
    result = await service.set_request_status(5, status="refunded", reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_update_return_request") == []


async def test_surec_durumu_ilerletilebilir() -> None:
    service, api, _ = _service()
    result = await service.set_request_status(5, status=calc.ST_RECEIVED, reason=GEREKCE,
                                              actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_update_return_request")[0]["payload"] == {"status": "received"}


async def test_musteri_bildirimi_siparis_notu_olarak_gider() -> None:
    service, api, _ = _service()
    result = await service.notify_customer(12, message="İadeniz onaylandı, kargo bekliyoruz.",
                                           reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("add_order_comment")[0]["notify"] is True


async def test_geri_gonderim_gonderi_secilmeden_baslatilamaz() -> None:
    service, api, _ = _service()
    result = await service.return_shipment(0, reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_return_shipment") == []


# =============================================================== rapor · CSV

async def test_csv_rapor_klasorune_yazilir(tmp_path: Path) -> None:
    service, api, _ = _service(export_path=str(tmp_path))
    api.refunds_payload = _refund_payload()
    result = await service.export_csv()
    assert result["ok"] is True
    written = Path(result["path"])
    assert written.parent == tmp_path
    assert "K-1" in written.read_text(encoding="utf-8")


async def test_iade_icmali_pdf_uretilir(tmp_path: Path) -> None:
    service, api, _ = _service(export_path=str(tmp_path))
    api.refunds_payload = _refund_payload()
    result = await service.build_report("summary", {})
    assert result["ok"] is True
    assert Path(result["path"]).read_bytes().startswith(b"%PDF")


async def test_bos_aralikta_icmal_uretilmez(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.build_report("summary", {})
    assert result["ok"] is False
    assert "iade yok" in result["error"]


async def test_iade_formu_siparis_secilmeden_uretilmez(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.build_report("form", {})
    assert result["ok"] is False


async def test_rapor_klasoru_disindaki_dosya_basilmaz(tmp_path: Path) -> None:
    # `lp` serbest yol kabul etseydi makinedeki her dosya kâğıda dökülebilirdi.
    printer = FakePrinter()
    service, _, _ = _service(printer=printer, export_path=str(tmp_path))
    outsider = tmp_path.parent / "disarida.pdf"
    outsider.write_bytes(b"%PDF-1.4")
    result = await service.print_report(str(outsider))
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]
    assert printer.printed == []


async def test_rapor_klasorundeki_dosya_basilir(tmp_path: Path) -> None:
    printer = FakePrinter()
    service, api, _ = _service(printer=printer, export_path=str(tmp_path))
    api.refunds_payload = _refund_payload()
    produced = await service.build_report("summary", {})
    result = await service.print_report(produced["path"])
    assert result["ok"] is True
    assert len(printer.printed) == 1


async def test_yazici_yoksa_basma_anlasilir_sekilde_reddedilir() -> None:
    service, _, _ = _service()
    assert (await service.printer_status())["ready"] is False
    assert (await service.print_report("/tmp/x.pdf"))["ok"] is False


# ================================================================== denetim

async def test_kredi_notlari_okunamazsa_csv_yazilmaz(tmp_path: Path) -> None:
    # Eksik listeyi arşivlemek, aylar sonra okunacak yanlış bir belge bırakır.
    service, api, _ = _service(export_path=str(tmp_path))
    api.fail.add("refunds")
    result = await service.export_csv()
    assert result["ok"] is False
    assert "dosya yazılmadı" in result["error"]
    assert list(tmp_path.iterdir()) == []


async def test_kredi_notlari_okunamazsa_icmal_uretilmez(tmp_path: Path) -> None:
    service, api, _ = _service(export_path=str(tmp_path))
    api.fail.add("refunds")
    result = await service.build_report("summary", {})
    assert result["ok"] is False
    assert "dosya yazılmadı" in result["error"]


# ====================================================== CANLI BİÇİM · uçtan uca

async def test_canli_bicimli_kredi_notu_listesi_musteriyi_ve_siparisi_gosterir() -> None:
    # Modül bir süre yalnız snake_case okuyordu: tablo açılıyor ama müşteri,
    # sipariş no ve adet sütunları boş görünüyordu.
    service, api, _ = _service()
    api.refunds_payload = {"items": [LIVE_REFUND], "meta": {"total": 1, "lastPage": 1}}
    result = await service.refunds(date_from="2026-07-01", date_to="2026-07-31")
    row = result["items"][0]
    assert row["customer"] == "veysel kemal TUNCER"
    assert row["orderNumber"] == "5"
    assert row["itemCount"] == 7
    assert row["amount"] == 839_300


async def test_canli_pano_bicimi_iade_oranini_hesaplatir() -> None:
    service, api, _ = _service()
    api.refunds_payload = {"items": [LIVE_REFUND], "meta": {"total": 1, "lastPage": 1}}
    api.stats_payload = LIVE_SALES_STATS       # zarfsız liste, `total_sales.current`
    result = await service.refunds()
    assert result["summary"]["salesTotal"] == 1_600
    assert result["summary"]["refundRate"] is not None
    assert result["summary"]["rateNote"] == ""


async def test_talep_taramasi_ucun_tanidigi_tarih_adlarini_gonderir() -> None:
    # `bbd/return-requests` süzgeci `from`/`to` der. Bir dönem `date_from`/
    # `date_to` gönderiliyordu: Laravel tanımadığı parametreyi atar, hata
    # ÇIKMAZ ve ekran seçilen aralığın dışındaki talepleri de listelerdi.
    service, api, _ = _service()
    await service.refunds(date_from="2026-08-01", date_to="2026-08-16")
    filters = api.args("bbd_return_requests")[0][0]
    assert filters["from"] == "2026-08-01"
    assert filters["to"] == "2026-08-16"
    assert "date_from" not in filters


async def test_canli_bicimli_talep_durumu_ve_siparis_numarasi_ekrana_dogru_gecer() -> None:
    # Uç YAYINDA; canlı yanıtta durum sözlük, başlıklar Türkçe. Eski okuma
    # rozete ham sözlük yazıyor ve "Sipariş" sütununu boş bırakıyordu.
    service, api, _ = _service()
    api.requests_payload = {"items": [LIVE_RETURN_REQUEST], "meta": {"lastPage": 1}}
    result = await service.refunds(date_from="2026-07-01", date_to="2026-07-31")
    assert result["sources"]["requests"]["available"] is True
    row = next(item for item in result["items"] if item["requestId"] == 2)
    assert row["statusLabel"] == "İade edildi"
    assert row["orderNumber"] == "11"
    assert row["reasonLabel"] == "Ayıplı"


async def test_bozuk_tarih_ekrani_dusurmez_ve_yok_sayildigi_soylenir() -> None:
    # Uç şeması yalnız uzunluğa bakıyor; `dateTo=abc` 500 ile ekranı düşürüyordu.
    service, _, _ = _service()
    result = await service.refunds(date_to="abc")
    assert result["ok"] is True
    assert result["range"]["to"] == calc.today_iso()
    assert any("Anlaşılmayan tarih" in item for item in result["warnings"])


# ================================================= kargo bedelinin ikinci iadesi

async def test_kargo_bedeli_kredi_notlarindan_dusulur() -> None:
    # Sipariş `shipping_amount_refunded` yayınlamıyor; kargo iki kez verilebilirdi.
    service, api, _ = _service()
    api.orders_by_id[5] = json.loads(json.dumps(LIVE_ORDER))
    api.refunds_payload = {"items": [{**LIVE_REFUND, "shippingAmount": 120}],
                           "meta": {"total": 1, "lastPage": 1}}
    result = await service.calculate(5, quantities={"5": 1}, refund_shipping=True)
    assert result["ok"] is True
    assert result["calc"]["shippingAvailable"] == 0
    assert result["calc"]["shipping"] == 0


async def test_kargo_dogrulanamazsa_hesap_susmaz() -> None:
    service, api, _ = _service()
    api.orders_by_id[5] = json.loads(json.dumps(LIVE_ORDER))
    api.fail.add("refunds")
    result = await service.calculate(5, quantities={"5": 1}, refund_shipping=True)
    assert result["ok"] is True
    assert any("ikinci kez iade etmediğinizden" in item for item in result["warnings"])


async def test_kargo_secilmediyse_gecmis_iadeler_bosuna_sorulmaz() -> None:
    # Dakikada 55 istek kovası: gereksiz çağrı ekranı kilitliyordu.
    service, api, _ = _service()
    api.orders_by_id[5] = json.loads(json.dumps(LIVE_ORDER))
    await service.calculate(5, quantities={"5": 1})
    assert api.used("refunds") == []


async def test_cekmecede_kargo_gecmis_iadelerden_hesaplanir() -> None:
    service, api, _ = _service()
    api.orders_by_id[5] = json.loads(json.dumps(LIVE_ORDER))
    api.refunds_payload = {"items": [{**LIVE_REFUND, "shippingAmount": 50}],
                           "meta": {"total": 1, "lastPage": 1}}
    card = await service.order_card(5)
    assert card["shippingVerified"] is True
    assert card["order"]["shippingRefunded"] == 5_000
    assert card["order"]["shippingAvailable"] == 7_000


async def test_gecmis_iadeler_okunamazsa_kargo_dogrulanmadigi_yazilir() -> None:
    service, api, _ = _service()
    api.orders_by_id[5] = json.loads(json.dumps(LIVE_ORDER))
    api.fail.add("refunds")
    card = await service.order_card(5)
    assert card["ok"] is True
    assert card["shippingVerified"] is False
    assert any("DOĞRULANAMADI" in item for item in card["warnings"])


# ============================================ para çıktıktan sonraki yerel hata

async def test_jeton_isaretlenemezse_para_ciktigi_acikca_soylenir() -> None:
    # UPDATE patlarsa istisna yukarı giderdi; kullanıcı "hata" görüp iadeyi
    # ikinci kez denerdi. Para çıktıktan sonra iş "başarılı"dır ve uyarı taşır.
    class BozukStore(FakeStore):
        async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            if "_calc" in sql and sql.strip().startswith("UPDATE"):
                raise RuntimeError("disk dolu")
            await super().execute(sql, params)

    service, api, _ = _service(store=BozukStore())
    preview = await service.calculate(12, quantities={"101": 1})
    result = await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali",
                                   dry_run=False)
    assert result["ok"] is True
    assert "İKİNCİ KEZ onaylamayın" in result["warning"]
    assert len(api.used("create_refund")) == 1


async def test_denetim_izi_siparise_gore_suzulur() -> None:
    service, _, store = _service()
    preview = await service.calculate(12, quantities={"101": 1})
    await service.approve(token=preview["token"], reason=GEREKCE, actor="Ali", dry_run=False)
    await service.notify_customer(12, message="İadeniz tamamlandı.", reason=GEREKCE,
                                  actor="Ali", dry_run=False)
    result = await service.audit(order_id=12)
    assert {row["action"] for row in result["items"]} == {"create_refund", "notify_customer"}
    assert all(row["reason"] == GEREKCE for row in result["items"])
    assert len(store.audit) == len(result["items"])


# ================================= POS iadesi: düğme TIKLANMADAN ÖNCE kapanır

async def test_pos_iadesinin_kapali_oldugu_kart_yanitinda_ilan_edilir() -> None:
    """Düğme her tıklamada aynı cümleyi tekrar etmesin diye önden kapanır.

    Geçit bu ucu BİLEREK yazmadı (para hareketi) ve istek hiç çıkmıyor. Ekran
    bunu ancak tıklandıktan sonra öğreniyordu; artık kart yanıtında yazıyor.
    POS LİSTESİ KALDI: hangi karta ne çekildiği gerçek bir bilgi ve iadeyi
    panelden yapacak kişinin ihtiyacı tam olarak budur.
    """
    service, _, _ = _service()
    card = await service.order_card(12)
    assert card["payments"]["refundBlocked"] is True
    assert "geri alınamaz" in card["payments"]["refundReason"]
    assert service.features()["posRefund"]["available"] is False
