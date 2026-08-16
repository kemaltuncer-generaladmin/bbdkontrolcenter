"""Manuel Yedekleme servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from store_backups_backend.service import BackupsService
from store_backups_fakes import FakeApi, FakeLog, FakeStore, FakeStoreApiError

YEDEK = "bbd-2026-08-13-0300.tar.gz"


def _iso(days: float = 0.0) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def _item(**fields: Any) -> dict[str, Any]:
    base = {"name": YEDEK, "created_at": _iso(0.2),
            "scope": ["database", "uploads", "config"], "size": 50 * 1024 * 1024,
            "duration": 42, "actor": "Ayşe", "note": "haftalık", "sha256": "abc",
            "verify_state": "ok", "path": "/srv/backups/" + YEDEK}
    base.update(fields)
    return base


class Bus:
    """Olay veri yolunun testlik yüzü."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             folder: str = "", **config: Any) -> tuple[BackupsService, FakeApi, FakeStore, Bus]:
    api = api or FakeApi([_item()])
    store = store or FakeStore()
    bus = Bus()
    service = BackupsService(
        api=api, store=store, log=FakeLog(), publish=bus,
        config={"export_path": folder, "stale_after_hours": 48, "delete_min_age_days": 30,
                "download_limit_mb": 200, **config},
        fallback_dir=Path(folder or tempfile.gettempdir()) / "km-test-yedek",
    )
    return service, api, store, bus


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _, _ = _service()
    api.fail.add("bbd_backups")
    result = await service.backups()
    assert result["ok"] is True                 # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]
    # Bağlantı yokken de kurallar ekrana gider: kullanıcı ne yapamayacağını bilir.
    assert result["policy"]["deleteAvailable"] is False
    assert "bilinmiyor" in result["diskNote"]


async def test_envanter_okunamayinca_yedek_yok_denmez() -> None:
    # En sinsi yalan: bağlantı koptuğunda durum bandının "Hiç yedek alınmamış.
    # Canlı mağaza verisi yedeksiz duruyor." demesi. Yedek dün gece alınmış
    # olabilir; bildiğimiz tek şey listeyi okuyamadığımız.
    service, api, _, _ = _service()
    api.fail.add("bbd_backups")
    band = (await service.backups())["summary"]
    assert band["known"] is False
    assert band["has"] is False
    assert "Hiç yedek alınmamış" not in band["text"]
    assert "BİLİNMİYOR" in band["text"]


async def test_uc_yayinda_degilse_magaza_coktu_denmez() -> None:
    # SAVUNMA DALI — canlıdaki güncel hâli değil. Uç 2026-08-16'da 200 dönüyor;
    # 404 hâli bir dönem gerçekti (paket henüz yayınlanmamıştı). Paket geri
    # çekilirse mağaza AYAKTA olduğu hâlde bu dal çalışır: eksik olan yalnız bu
    # ekranın konuştuğu paket olur. İkisini aynı cümleyle anlatmak, personeli
    # boşuna sunucu odasına gönderir — dal bu yüzden sınanmaya devam eder (K7).
    service, api, _, _ = _service()
    api.missing.add("bbd_backups")
    result = await service.backups()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["endpointPending"] is True
    assert "henüz yayında değil" in result["error"]
    assert "Mağazaya ulaşılamadı" not in result["error"]


async def test_uc_yayinda_degilse_yedek_alma_dugmesi_kapanir() -> None:
    # Sessiz ölü düğme bırakılmaz: kullanıcıya gerekçe yazdırıp onaylatıp
    # sonra 404 göstermek, olmayan düğmeden kötüdür.
    service, api, _, _ = _service()
    api.missing.add("bbd_backups")
    policy = (await service.backups())["policy"]
    assert policy["createAvailable"] is False
    assert policy["endpointPending"] is True
    assert policy["endpointNote"]
    # Uç yayına girdiği gün düğme kendiliğinden açılır; elle ayar gerekmez.
    assert (await _service()[0].backups())["policy"]["createAvailable"] is True


async def test_kontrol_api_kapaliysa_magazaya_ulasilamadi_denmez() -> None:
    # SAVUNMA DALI — canlıdaki güncel hâli değil. 2026-08-13'te GET
    # /api/admin/bbd/backups 503 {"error":{"code":"CONTROL_API_DISABLED",...}}
    # dönüyordu (aynı anda /settings/channels ve /orders 200 — MAĞAZA AYAKTA);
    # 2026-08-16'da uç 200 dönüyor, yani anahtar açılmış. Test KALIYOR: anahtar
    # bir dağıtımda geri kapanabilir. Geçit 503'e `code="server"` verdiği için
    # yalnız `bbd_endpoint_missing` koduna bakan eski kod bunu "gerçek arıza"
    # sayıyor ve ekrana "Mağazaya ulaşılamadı" yazdırıyordu: ayakta olan bir
    # mağaza için personeli sunucu odasına gönderen tam da o cümle.
    service, api, _, _ = _service()
    api.disabled.add("bbd_backups")
    result = await service.backups()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["endpointPending"] is True
    assert result["endpointState"] == "disabled"
    assert "Mağazaya ulaşılamadı" not in result["error"]
    # Çare SÖYLENİR: kapalı olan bir anahtar, arızalı bir sunucu değil.
    assert "BBD_CONTROL_API_ENABLED" in result["error"]


async def test_kontrol_api_kapaliyken_yedek_alma_dugmesi_kapanir() -> None:
    # Düğme açık kalsaydı kullanıcı kapsam seçer, gerekçe yazar, onaylar ve
    # sonunda 503 görürdü — sessiz ölü düğmenin en pahalı biçimi.
    service, api, _, _ = _service()
    api.disabled.add("bbd_backups")
    policy = (await service.backups())["policy"]
    assert policy["createAvailable"] is False
    assert policy["endpointState"] == "disabled"
    assert "BBD_CONTROL_API_ENABLED" in policy["endpointNote"]


async def test_uc_yok_ile_uc_kapali_ayri_cumlelerle_anlatilir() -> None:
    # İkisi de "yayında değil" ama ÇARELERİ farklı: 404'te yapılacak bir şey
    # yok, 503'te açılacak bir anahtar var. Aynı cümleyi yazmak, açılabilecek
    # bir anahtarı "bekleyin" diye göstermek olurdu.
    yok, yok_api, _, _ = _service()
    yok_api.missing.add("bbd_backups")
    kapali, kapali_api, _, _ = _service()
    kapali_api.disabled.add("bbd_backups")

    a = (await yok.backups())["error"]
    b = (await kapali.backups())["error"]
    assert a != b
    assert "BBD_CONTROL_API_ENABLED" not in a
    assert "BBD_CONTROL_API_ENABLED" in b


async def test_gercek_sunucu_hatasi_uc_yayinda_degil_sayilmaz() -> None:
    # Ters yönde yalan da yasak: her 5xx'i "uç kapalı" saymak, gerçekten çöken
    # bir mağazayı "anahtar kapalı, sunucuya gitmeyin" diye anlatırdı.
    service, api, _, _ = _service()

    async def patla() -> dict[str, Any]:
        raise FakeStoreApiError("Mağaza hata verdi (500): Internal Server Error",
                                code="server", status=500)

    api.bbd_backups = patla                                  # type: ignore[method-assign]
    result = await service.backups()
    assert result["endpointPending"] is False
    assert result["endpointState"] == "live"
    assert result["policy"]["createAvailable"] is True
    assert "500" in result["error"]


async def test_kontrol_api_kapaliyken_yazma_denemesi_cozumu_soyler() -> None:
    service, api, store, _ = _service()
    api.disabled.update({"bbd_create_backup", "bbd_backups"})
    result = await service.create(scope=["database"], reason="Sürüm öncesi tam yedek",
                                  actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "BBD_CONTROL_API_ENABLED" in result["error"]
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]


async def test_uc_yayinda_degilken_yazma_denemesi_anlasilir_konusur() -> None:
    service, api, store, _ = _service()
    api.missing.update({"bbd_create_backup", "bbd_backups"})
    result = await service.create(scope=["database"], reason="Sürüm öncesi tam yedek",
                                  actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "henüz yayında değil" in result["error"]
    # Ne yapmaya çalıştığımız yine de yerel ize yazılır (ağ koparsa tek kayıt bu).
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]


async def test_durum_bandi_ve_disk_listeyle_birlikte_doner() -> None:
    service, api, _, _ = _service()
    api.meta = {"disk": {"total": 100 * 1024**3, "free": 40 * 1024**3}}
    result = await service.backups()
    assert result["summary"]["tone"] == "good"
    assert result["disk"]["known"] is True
    assert "yedek daha alınabilir" in result["diskNote"]


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _, _ = _service()
    # `restore` LİSTEDE YOK: o iş bu geçitten hiç yapılmıyor ve gerekçenin
    # uzunluğuna bakılmadan reddediliyor (aşağıdaki geri yükleme testleri).
    for result in (await service.create(scope=["database"], reason="ok", actor="Ali"),
                   await service.delete(YEDEK, reason="ok", actor="Ali")):
        assert result["ok"] is False
        assert "Gerekçe" in result["error"]
    assert api.calls == []


async def test_kapsamsiz_yedek_alinmaz() -> None:
    service, api, _, _ = _service()
    result = await service.create(scope=["bilinmeyen"], reason="Elle yedek alınıyor test",
                                  actor="Ali")
    assert result["ok"] is False
    assert "kapsam" in result["error"]
    assert api.used("bbd_create_backup") == []


# ================================================================ yedek alma

async def test_yedek_alinirken_kapsam_suzulur_ve_not_tasinir() -> None:
    service, api, store, _ = _service()
    result = await service.create(scope=["config", "database", "secrets"],
                                  note="sürüm yükseltmesi öncesi",
                                  reason="Sürüm yükseltmesi öncesi tam yedek",
                                  actor="Ayşe Yılmaz", dry_run=False)
    assert result["ok"] is True
    assert result["scope"] == ["database", "config"]        # sıra sabit, bilinmeyen atıldı
    call = api.used("bbd_create_backup")[0]
    assert call["scope"] == ["database", "config"]
    assert call["note"] == "sürüm yükseltmesi öncesi"
    assert call["dry_run"] is False
    entry = [row for row in store.audit if row["action"] == "create"][-1]
    assert entry["reason"] == "Sürüm yükseltmesi öncesi tam yedek"
    assert entry["actor"] == "Ayşe Yılmaz"
    assert entry["result"] == "ok"


async def test_yedek_alma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store, _ = _service()
    api.fail.add("bbd_create_backup")
    result = await service.create(scope=["database"], reason="Gece yedeği elle alınıyor",
                                  actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]


async def test_kuru_provada_olay_yayimlanmaz() -> None:
    service, _, _, bus = _service()
    await service.create(scope=["database"], reason="Kuru prova denemesi yapıldı", actor="Ali",
                         dry_run=True)
    assert bus.events == []
    await service.create(scope=["database"], reason="Gerçek yedek alınıyor şimdi", actor="Ali",
                         dry_run=False)
    assert bus.events[0][0] == "store.backup.created"


async def test_yayimlanan_olay_ilan_edilen_yuku_tasir() -> None:
    # module.yaml `{name, scope, bytes, actor, dryRun}` ilan ediyor. Eksik alan
    # dinleyicide KeyError'dır ve K3 gereği iletişimin tamamı bu yükün üstünde.
    service, _, _, bus = _service()
    await service.create(scope=["database"], reason="İlan edilen yük sınanıyor", actor="Ali",
                         dry_run=False)
    _, payload = bus.events[0]
    assert set(payload) == {"name", "scope", "bytes", "actor", "dryRun"}
    assert payload["dryRun"] is False


# ================================================================ doğrulama

async def test_dogrulama_kullanicidan_gerekce_istemez_ama_ize_yazar() -> None:
    # Doğrulama okuma işidir; her seferinde "neden doğruluyorsun" diye sormak
    # gerekçe alanını anlamsızlaştırır. Geçit yine de gerekçe istiyor.
    service, api, store, _ = _service()
    result = await service.verify(YEDEK, actor="Ali")
    assert result["ok"] is True
    assert result["state"] == "ok"
    assert result["sha256"] == "abc123"
    assert len(api.used("bbd_verify_backup")[0]["reason"]) >= 10
    assert store.audit[-1]["action"] == "verify"


async def test_bozuk_yedek_dogrulamada_acikca_soylenir() -> None:
    service, api, _, _ = _service()
    api.verify_payload = {"verify_state": "corrupt"}
    result = await service.verify(YEDEK, actor="Ali")
    assert result["ok"] is False
    assert "geri yüklenmemeli" in result["message"]


async def test_bozuk_ad_dogrulamaya_bile_gitmez() -> None:
    service, api, _, _ = _service()
    result = await service.verify("../../etc/passwd", actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_verify_backup") == []


# ============================================================ GERİ YÜKLEME

async def test_geri_yukleme_istek_cikmadan_reddedilir_ve_nedenini_soyler() -> None:
    """Geçit bu ucu BİLEREK yazmadı: geri yükleme canlı veriyi ezer.

    Yedek almak, doğrulamak ve indirmek geri alınabilir işlerdir ve bu ekrandan
    yapılır; yedeği geri yüklemek 1.422 ürünü, siparişleri ve müşteri
    kayıtlarını yedek anındaki hâline döndürür.
    """
    service, api, store, bus = _service()
    result = await service.restore(YEDEK, reason="Hatalı toplu güncelleme geri alınıyor",
                                   actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["feature"] == "restore"
    assert "CANLI VERİYİ EZER" in result["error"]
    assert api.calls == []
    assert bus.events == []
    # Ne yapılmak istendiği yerel izde KALIR.
    assert [row["result"] for row in store.audit if row["action"] == "restore"] == ["uc_yok"]


async def test_geri_yukleme_denemesi_bosuna_guvenlik_yedegi_ALMAZ() -> None:
    """Eskiden her deneme gerçek bir tam yedek üretiyordu.

    Sıra doğruydu (önce güvenlik yedeği, sonra geri yükleme) ama zincirin
    sonundaki uç zaten yoktu: yapılmayacak bir iş için disk doluyor, mağaza
    meşgul ediliyor ve kullanıcı sonunda ham bir hata metni görüyordu.
    """
    service, api, _, _ = _service()
    await service.restore(YEDEK, reason="Hatalı toplu güncelleme geri alınıyor",
                          actor="Ali", dry_run=False)
    assert api.used("bbd_create_backup") == []
    assert api.used("bbd_restore_backup") == []


async def test_kapali_isin_nedeni_ilan_edilir() -> None:
    """Ekran düğmeyi TIKLANMADAN ÖNCE kapatabilmeli; nedeni yazılı olmalı."""
    service, _, _, _ = _service()
    features = service.features()
    assert features["restore"]["available"] is False
    assert len(features["restore"]["reason"]) > 40
    for key in ("create", "verify", "download"):
        assert features[key]["available"] is True


# ================================================================== silme

async def test_yeni_yedek_silinemez_gerekce_verilse_bile() -> None:
    service, _, _, _ = _service()
    result = await service.delete(YEDEK, reason="Yer açmak için siliniyor bu", actor="Ali")
    assert result["ok"] is False
    assert "30 günden eski" in result["error"]


async def test_eski_yedekte_bile_silme_ucu_yoksa_acikca_soylenir() -> None:
    # Sessizce patlamaz: kullanıcı düğmenin neden çalışmadığını okur.
    service, _, store, _ = _service(FakeApi([_item(created_at=_iso(60))]))
    result = await service.delete(YEDEK, reason="Arşiv temizliği yapılıyor bugün", actor="Ali")
    assert result["ok"] is False
    assert result["code"] == "endpoint_missing"
    assert "henüz yayında değil" in result["error"]
    assert store.audit[-1]["result"] == "uc_yok"


# ================================================================== indirme

async def test_buyuk_yedek_indirilmez_sunucudaki_yol_gosterilir() -> None:
    # `bbd_download_backup` ham baytı belleğe alır; 200 MB üstü dosyayı
    # sidecar'dan geçirmek makineyi takar.
    service, api, _, _ = _service(FakeApi([_item(size=900 * 1024 * 1024)]))
    result = await service.download(YEDEK, actor="Ali")
    assert result["ok"] is False
    assert "/srv/backups/" in result["error"]
    assert api.used("bbd_download_backup") == []


async def test_indirilen_yedek_diske_0600_yazilir() -> None:
    with tempfile.TemporaryDirectory(prefix="km-yedek-test-") as folder:
        service, api, store, _ = _service(folder=folder)
        result = await service.download(YEDEK, actor="Ali")
        assert result["ok"] is True
        written = Path(result["path"])
        assert written.read_bytes() == b"yedek-icerigi"
        assert stat.S_IMODE(written.stat().st_mode) == 0o600
        assert api.count("bbd_download_backup") == 1
        assert store.audit[-1]["action"] == "download"


# ==================================================================== CSV

async def test_envanter_csvsi_rapor_rafina_yazilir() -> None:
    with tempfile.TemporaryDirectory(prefix="km-yedek-test-") as folder:
        service, _, _, _ = _service(folder=folder)
        result = await service.export_csv()
        assert result["ok"] is True
        assert result["rows"] == 1
        content = Path(result["path"]).read_bytes().decode("utf-8-sig")
        assert "Doğrulama" in content
        assert YEDEK in content


async def test_bos_envanterde_csv_uretilmez() -> None:
    service, _, _, _ = _service(FakeApi([]))
    result = await service.export_csv()
    assert result["ok"] is False
    assert "yedek yok" in result["error"]


# ============================================================== denetim izi

async def test_denetim_izi_yedek_adina_gore_suzulur() -> None:
    service, _, _, _ = _service()
    await service.verify(YEDEK, actor="Ali")
    await service.create(scope=["database"], reason="Başka bir yedek alınıyor", actor="Ali",
                         dry_run=False)
    everything = await service.audit()
    assert len(everything["items"]) >= 3
    filtered = await service.audit(name=YEDEK)
    assert all(row["name"] == YEDEK for row in filtered["items"])


async def test_denetim_ayrintisi_json_olarak_saklanir() -> None:
    service, _, store, _ = _service()
    await service.create(scope=["database"], reason="Yalnız veritabanı yedekleniyor",
                         actor="Ali", dry_run=False)
    detail = json.loads(store.audit[-1]["detail"])
    assert detail["scope"] == ["database"]


# =========================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_yol_bizim_sayilmaz() -> None:
    # Serbest yol kabul etmek, bu ekran üzerinden makinedeki herhangi bir
    # dosyayı dolaştırmaya açık kapı bırakır.
    with tempfile.TemporaryDirectory(prefix="km-yedek-test-") as folder:
        service, _, _, _ = _service(folder=folder)
        downloaded = await service.download(YEDEK, actor="Ali")
        assert service.within_output_dir(downloaded["path"]) is True
        assert service.within_output_dir("/etc/passwd") is False
