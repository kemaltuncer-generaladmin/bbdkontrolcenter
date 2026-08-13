"""UDİT servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from store_udit_logs_backend.service import AuditProvider, AuditService
from store_udit_logs_fakes import FakeApi, FakeLog, FakeStore

BUGUN = "2026-08-13"


def _utc_of(hour: int, minute: int) -> str:
    """Yerel saat `hour:minute` anını GEÇİDİN yazdığı biçimde (UTC) üretir.

    Testler makinenin saat diliminden bağımsız olmalı: geçit satırları her
    zaman UTC yazar ve servis onları yerel saate çevirir. Damgayı elle
    `+00:00` yazmak, testi yalnız Türkiye saatinde doğru kılardı.
    """
    local = datetime.fromisoformat(f"{BUGUN}T{hour:02d}:{minute:02d}:00").astimezone()
    return local.astimezone(UTC).isoformat(timespec="seconds")


def _remote(entry_id: int, minute: int, **over: Any) -> dict[str, Any]:
    row = {
        "id": entry_id,
        "auditable_type": "Webkul\\Catalog\\Models\\Product",
        "auditable_id": 5,
        "action": "updated",
        "old_values": {"price": "10.00"},
        "new_values": {"price": "12.00"},
        "user": {"name": "Ayşe"},
        "ip_address": "10.0.0.4",
        "created_at": f"{BUGUN} 10:{minute:02d}:00",
    }
    row.update(over)
    return row


def _local(request_id: str, minute: int, **over: Any) -> dict[str, Any]:
    row = {
        "id": minute,
        "request_id": request_id,
        "method": "PUT",
        "path": "/api/admin/catalog/products/5",
        "action": "update_product",
        "reason": "Fiyat listesi güncellendi",
        "actor": "Ayşe",
        "dry_run": 0,
        "body": {"price": "12.00"},
        "result": "ok",
        "status": 200,
        # Geçit UTC yazar; yerel saate çevrildiğinde mağaza damgasıyla aynı
        # eksene gelmeli (bkz. records testi). `minute` YEREL dakikadır.
        "created_at": _utc_of(10, minute),
    }
    row.update(over)
    return row


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[AuditService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = AuditService(
        api=api, store=store, log=FakeLog(),
        config={"page_size": 100, "max_range_days": 92, "local_scan_limit": 800, **config},
        fallback_dir=Path(tempfile.gettempdir()) / "km-test-denetim",
    )
    return service, api, store


# ==================================================== zorunlu tarih aralığı

async def test_tarih_araligi_olmadan_sorgu_atilmaz() -> None:
    service, api, _ = _service()
    result = await service.entries(start="", end="")
    assert result["ok"] is True          # uç patlamaz
    assert result["guard"] is True
    assert result["items"] == []
    assert "Tarih aralığı zorunludur" in result["error"]
    assert api.calls == []               # mağazaya HİÇ gidilmedi


async def test_cok_genis_aralik_reddedilir_ve_nedenini_soyler() -> None:
    service, api, _ = _service(max_range_days=30)
    result = await service.entries(start="2026-01-01", end="2026-08-13")
    assert result["guard"] is True
    assert "en çok 30 gün" in result["error"]
    assert api.calls == []


async def test_bozuk_imlec_sessizce_basa_donmez() -> None:
    service, _, _ = _service()
    result = await service.entries(start=BUGUN, end=BUGUN, cursor="bu-imleç-değil!!")
    assert result["guard"] is True
    assert "İmleç okunamadı" in result["error"]


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_gecidin_yerel_izi_gosterilir() -> None:
    api = FakeApi(remote=[_remote(1, 10)], local=[_local("a", 5, result="blocked")])
    api.fail.add("bbd_audit")
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN)

    assert result["ok"] is True
    assert result["connected"] is False
    assert "patladı" in result["error"]
    # Ekran boş kalmaz: "ne yapmaya çalıştık" kaydı tam da o an gerekir.
    assert [row["source"] for row in result["items"]] == ["gateway"]


async def test_gecit_izi_okunamazsa_uzak_liste_yine_gelir() -> None:
    api = FakeApi(remote=[_remote(1, 10)])
    api.fail.add("audit_trail")
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN)

    assert result["connected"] is True
    assert len(result["items"]) == 1
    assert any("Geçit izi okunamadı" in warning for warning in result["warnings"])


# ================================================= iki kaynağın birleşimi

async def test_basarili_yazma_listede_tek_satirdir_ve_gerekcesini_tasir() -> None:
    # Aynı işlem iki kaynakta da var: uzak satır alan farkını, geçit satırı
    # gerekçeyi taşıyor. İkisi ayrı satır olarak gösterilirse liste çiftlenir.
    api = FakeApi(remote=[_remote(1, 10, request_id="a")], local=[_local("a", 10)])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN)

    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["source"] == "store"
    assert row["reason"] == "Fiyat listesi güncellendi"
    assert row["summary"] == "price"


async def test_gonderilemeyen_istek_yalniz_gecit_izinde_kalir() -> None:
    api = FakeApi(remote=[], local=[_local("b", 9, result="", reason="Ağ koptu denemesi")])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN)

    row = result["items"][0]
    assert row["source"] == "gateway"
    assert row["resultLabel"] == "Bilinmiyor"


async def test_iki_kaynak_zamana_gore_ic_ice_dizilir() -> None:
    api = FakeApi(remote=[_remote(2, 20), _remote(1, 5)],
                  local=[_local("x", 10, result="blocked")])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN)

    assert [row["key"] for row in result["items"]] == ["r:2", "g:x", "r:1"]


# ================================================ imleçli sunucu sayfalaması

async def test_ikinci_sayfa_ilk_sayfanin_satirlarini_tekrar_etmez() -> None:
    api = FakeApi(remote=[_remote(index, 59 - index) for index in range(1, 13)])
    service, _, _ = _service(api, page_size=25)

    first = await service.entries(start=BUGUN, end=BUGUN, size=5)
    assert first["hasMore"] is True
    assert first["cursor"]

    second = await service.entries(start=BUGUN, end=BUGUN, size=5, cursor=first["cursor"])
    ilk = {row["key"] for row in first["items"]}
    ikinci = {row["key"] for row in second["items"]}
    assert not (ilk & ikinci)
    assert len(ilk) == len(ikinci) == 5


async def test_yuz_satirlik_sayfa_iki_uzak_istekle_dolar() -> None:
    # Mağaza sayfa boyunu 50'de kırpıyor (store_api/paging.py MAX_PER_PAGE);
    # 100 istemek "yarım sayfa aldım ama hepsi sandım" hatasını üretirdi.
    api = FakeApi(remote=[_remote(index, index % 60) for index in range(1, 121)])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN, size=100)

    assert len(result["items"]) == 100
    sayfalar = [kwargs["page"] for kwargs in api.used("bbd_audit")]
    assert sayfalar == [1, 2]
    assert {kwargs["per_page"] for kwargs in api.used("bbd_audit")} == {50}


async def test_son_sayfada_imlec_kapanir() -> None:
    api = FakeApi(remote=[_remote(1, 10), _remote(2, 11)])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN, size=50)

    assert result["hasMore"] is False
    assert result["cursor"] == ""


# ===================================== sunucu süzgeci uygulamadıysa (K9 refleksi)

async def test_sunucu_suzgeci_yok_sayarsa_ekran_soyler_ve_kendisi_suzer() -> None:
    api = FakeApi(remote=[_remote(1, 10, auditable_id=5), _remote(2, 11, auditable_id=99)])
    api.ignore_filters = True
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN, entity="product", entity_id=5,
                                   ip="10.0.0.4", result="ok")

    assert [row["entityId"] for row in result["items"]] == [5]
    assert result["serverFiltered"] is False
    assert any("sayfa içinde süzüldü" in warning for warning in result["warnings"])


async def test_gerekce_metninde_arama_gecit_satirini_da_kapsar() -> None:
    api = FakeApi(remote=[_remote(1, 10)],
                  local=[_local("z", 9, result="blocked", reason="Yanlış fiyat girildi")])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN, q="yanlis fiyat")

    assert [row["key"] for row in result["items"]] == ["g:z"]


# ================================================================ ayrıntı

async def test_kayit_anahtari_kaynagi_da_soyler() -> None:
    api = FakeApi(remote=[_remote(7, 10, request_id="a")], local=[_local("a", 10)])
    service, _, _ = _service(api)

    uzak = await service.entry("r:7")
    assert uzak["ok"] is True
    assert uzak["item"]["reason"] == "Fiyat listesi güncellendi"

    yerel = await service.entry("g:a")
    assert yerel["ok"] is True
    assert yerel["item"]["source"] == "gateway"

    assert (await service.entry("7"))["ok"] is False


# ================================================== store.audit.for yeteneği

async def test_yetenek_bir_kaydin_gecmisini_dar_imzayla_verir() -> None:
    api = FakeApi(remote=[_remote(1, 10, auditable_id=5), _remote(2, 11, auditable_id=9)])
    service, _, _ = _service(api)
    provider = AuditProvider(service)

    result = await provider.for_record("product", 5, limit=10)

    assert result["ok"] is True
    assert [row["entityId"] for row in result["items"]] == [5]
    assert result["readOnly"] is True
    assert result["screen"] == "store_udit_logs"


async def test_yetenek_de_acik_uclu_sorgu_atmaz() -> None:
    service, _, _ = _service(max_range_days=30)
    provider = AuditProvider(service)

    result = await provider("product", 5)

    assert result["range"]["days"] == 30      # tavan yetenekte de geçerli


# =================================================== döküm ve yol güvenliği

async def test_csv_dokumu_rapor_klasorune_yazilir_ve_iz_birakir() -> None:
    api = FakeApi(remote=[_remote(1, 10), _remote(2, 11)])
    with tempfile.TemporaryDirectory() as folder:
        service, _, store = _service(api, export_path=folder)
        result = await service.export_csv({"start": BUGUN, "end": BUGUN}, actor="Ayşe")

        assert result["ok"] is True
        assert result["rows"] == 2
        assert Path(result["path"]).exists()
        # Kaydı okumak serbesttir; dışarı çıkarmak izlenir.
        assert store.exports[0]["kind"] == "csv"
        assert store.exports[0]["actor"] == "Ayşe"


async def test_dokum_tarih_araligi_olmadan_uretilmez() -> None:
    service, _, _ = _service()
    result = await service.export_csv({"start": "", "end": ""})
    assert result["ok"] is False
    assert "Tarih aralığı" in result["error"]


async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class Printer:
        def __init__(self) -> None:
            self.printed: list[Path] = []

        async def print_file(self, path: Path, *, title: str = "",
                             copies: int = 1) -> dict[str, Any]:
            self.printed.append(path)
            return {"printer": "HP"}

        async def status(self) -> dict[str, Any]:
            return {"ready": True}

    printer = Printer()
    with tempfile.TemporaryDirectory() as folder:
        service = AuditService(api=FakeApi(), store=FakeStore(), log=FakeLog(),
                               config={"export_path": folder}, printer=printer,
                               fallback_dir=Path(folder))
        with tempfile.NamedTemporaryFile(suffix=".pdf") as outside:
            result = await service.print_report(outside.name)

        assert result["ok"] is False
        assert "rapor klasöründe değil" in result["error"]
        assert printer.printed == []


async def test_bos_aralikta_dokum_uretilmez() -> None:
    service, _, _ = _service()
    result = await service.build_report("dump", {"start": BUGUN, "end": BUGUN})
    assert result["ok"] is False
    assert "denetim kaydı yok" in result["error"]


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    assert (await service.build_report("her-sey", {}))["ok"] is False


# ============ tamamen elenen sayfa listeyi KİLİTLEMEZ (mağaza süzmediğinde)

async def test_tamamen_elenen_sayfa_imleci_oldurmez() -> None:
    # Mağaza süzgeci uygulamadığında (Laravel tanımadığı parametreyi sessizce
    # yok sayar) bir sayfanın TÜM satırları burada elenebilir. İmleci boş
    # döndürmek listeyi orada kilitlerdi: "Sonraki sayfa" düğmesi ilk sayfayı
    # yeniden yükler, döküm de "kayıt yok" derdi — oysa kayıt bir sayfa ötede.
    api = FakeApi(remote=[_remote(index, index % 60, auditable_id=99)
                          for index in range(1, 51)]
                  + [_remote(index, index % 60, auditable_id=5)
                     for index in range(51, 101)])
    api.ignore_filters = True
    service, _, _ = _service(api, page_size=50)

    first = await service.entries(start=BUGUN, end=BUGUN, entity_id=5)

    assert first["items"] == []          # ilk 50 satırın hepsi elendi
    assert first["hasMore"] is True
    assert first["cursor"], "elenen sayfa imleci öldürmemeli"

    second = await service.entries(start=BUGUN, end=BUGUN, entity_id=5,
                                   cursor=first["cursor"])
    assert len(second["items"]) == 50
    assert {row["entityId"] for row in second["items"]} == {5}


async def test_dokum_elenen_sayfanin_otesini_de_tarar() -> None:
    # Aynı hatanın rapor tarafındaki yüzü: `_scan` imleci kaybedince döküm
    # sessizce "bu aralıkta kayıt yok" derdi. Sessiz eksik döküm, denetim
    # ekranında yanlış dökümden beterdir.
    api = FakeApi(remote=[_remote(index, index % 60, auditable_id=99)
                          for index in range(1, 51)]
                  + [_remote(index, index % 60, auditable_id=5)
                     for index in range(51, 101)])
    api.ignore_filters = True
    with tempfile.TemporaryDirectory() as folder:
        service, _, _ = _service(api, export_path=folder, page_size=50)
        result = await service.export_csv({"start": BUGUN, "end": BUGUN, "entityId": 5})

    assert result["ok"] is True
    assert result["rows"] == 50


async def test_yalniz_kaynak_suzgeci_sunucuyu_suclamaz() -> None:
    # `source` mağazaya hiç gönderilmez; onunla eleme yapıldığında "mağaza
    # süzgeci uygulamadı" uyarısı ASILMAMALI — uç doğru çalışıyor olabilir.
    api = FakeApi(remote=[_remote(1, 10)], local=[_local("s", 9, result="blocked")])
    service, _, _ = _service(api)

    result = await service.entries(start=BUGUN, end=BUGUN, source="gateway")

    assert [row["source"] for row in result["items"]] == ["gateway"]
    assert result["serverFiltered"] is True
    assert result["warnings"] == []


# ================================================ store.audit.for sınırları

async def test_yetenek_varlik_adi_olmadan_tum_kaydi_dokmez() -> None:
    # Boş varlık adı geçen bir panelin çekmecesine mağazanın TAMAMINI dökmek
    # sessiz bir felakettir: yetenek "bir kaydın geçmişi" sözleşmesidir.
    api = FakeApi(remote=[_remote(1, 10), _remote(2, 11)])
    service, _, _ = _service(api)
    provider = AuditProvider(service)

    result = await provider.for_record("", 0)

    assert result["ok"] is False
    assert result["items"] == []
    assert "varlık adı" in result["error"].lower()
    assert api.calls == []               # mağazaya HİÇ gidilmedi


# =================================================== numarasız mağaza kaydı

async def test_numarasiz_magaza_kaydi_cekmeceyi_hataya_dusurmez() -> None:
    # `r:<damga>-<varlık>-<no>` anahtarı tazelenemez ama listedeki kırpılmış
    # hâl DOĞRUDUR: ekran sebebini söyler, kendini boşaltmaz.
    service, api, _ = _service()
    result = await service.entry("r:2026-08-13T10:00:00-product-5")

    assert result["ok"] is True
    assert result["item"] is None
    assert "numara taşımıyor" in result["error"]
    assert api.calls == []


async def test_sayfa_butcesi_dolan_dokum_eksik_oldugunu_soyler() -> None:
    # Mağaza süzgeci uygulamazsa eşleşen satır çıkmadan sayfalar tükenir.
    # Bütçeyi sessizce harcayıp "tam döküm" demek, eksik dökümü tam diye
    # imzalatmak olurdu.
    from store_udit_logs_backend import service as module

    api = FakeApi(remote=[_remote(index, index % 60, auditable_id=99)
                          for index in range(1, 7_001)])
    api.ignore_filters = True
    with tempfile.TemporaryDirectory() as folder:
        srv, _, _ = _service(api, export_path=folder)
        rows, truncated, _ = await srv._scan({"start": BUGUN, "end": BUGUN, "entity_id": 5})

    assert rows == []
    assert truncated is True
    # Bütçe gerçekten sınırlı: sınırsız tarama geçidin hız kovasını kilitler.
    assert len(api.used("bbd_audit")) <= module.MAX_SCAN_PAGES * module.MAX_REMOTE_PAGES
