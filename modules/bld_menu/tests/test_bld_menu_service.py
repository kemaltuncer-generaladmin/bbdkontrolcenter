"""Menü servisi — iş kuralları. Ağa çıkmaz; `bld.api` taklit edilir."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from bld_menu_backend.service import MenuService
from bld_menu_fakes import DAY, DRAFT_DAY, FakeApi, FakeBus, FakeLog, FakeStore

GEREKCE = "Tavuk tedariki azaldı, sote tavanı 60'a çekildi"
ACTOR = "Ayşe Yılmaz"

#: Testlerde kullanılan gün. Sözleşme örneğiyle aynı.
TARIH = "2026-08-17"


def _service(*, day: dict[str, Any] | None = None,
             **config: Any) -> tuple[MenuService, FakeApi, FakeStore, FakeBus]:
    api = FakeApi(day=dict(DAY) if day is None else day)
    store = FakeStore()
    bus = FakeBus()
    service = MenuService(api=api, store=store, log=FakeLog(), config=dict(config),
                          publish=bus)
    return service, api, store, bus


def _yarin() -> str:
    """Bugünden sonraki bir gün — "geçmişe menü kurulamaz" kapısını aşmak için.

    Sabit bir tarih yazılmadı: takvim ilerledikçe sabit tarih geçmişte kalır ve
    test bir gün kendiliğinden kırmızıya döner.
    """
    return (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%d")


# ====================================================== K7 — ekran ayakta kalır

async def test_gecit_duserse_takvim_ayakta_kalir() -> None:
    # Satış devam ediyor. Yönetici "sunucuya ulaşılamıyor" ile "o güne menü
    # girilmemiş" arasındaki farkı GÖRMEK zorunda; boş ızgara ikisini de aynı
    # gösterirdi.
    service, api, _, _ = _service()
    api.fail.add("menu_calendar")
    sonuc = await service.calendar(date_from="2026-08-17", date_to="2026-08-23")
    assert sonuc["ok"] is True                 # uç patlamaz
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    assert sonuc["error"]
    # Sözleşme YEREL: geçit düşse bile rozetler ve açıklama listesi çizilebilir.
    assert len(sonuc["statuses"]) == 2
    assert len(sonuc["not_orderable"]) == 5


async def test_gecit_duserse_gun_stok_ve_urun_uclari_da_ayakta_kalir() -> None:
    service, api, _, _ = _service()
    api.fail.update({"menu_day", "menu_stock", "product_picker"})
    for cagri in (service.day(TARIH), service.stock(TARIH), service.products()):
        sonuc = await cagri
        assert sonuc["ok"] is True
        assert sonuc["connected"] is False
        assert sonuc["error"]


async def test_uc_henuz_yayinda_degilse_ekran_bunu_soyler() -> None:
    # Geçit `control_endpoint_missing` veriyor ve bu "kayıt yok"tan AYRI bir
    # şey: sunucu güncellendiğinde veri kendiliğinden gelecek.
    service, api, _, _ = _service()
    api.fail.add("menu_calendar")
    api.fail_code = "control_endpoint_missing"
    sonuc = await service.calendar(date_from="2026-08-17", date_to="2026-08-23")
    assert sonuc["endpoint_missing"] is True
    assert "henüz yayında değil" in sonuc["error"]


async def test_menusu_olmayan_gun_hata_degil() -> None:
    # "Menü yok" ile "boş menü var" ayırt edilebilmeli: ekran birincisinde
    # "gün kur", ikincisinde "kalem ekle" gösterir.
    service, _, _, _ = _service()
    sonuc = await service.day("2026-08-22")
    assert sonuc["ok"] is True
    assert sonuc["connected"] is True
    assert sonuc["exists"] is False
    assert sonuc["day"] == {}


# =========================================================== takvim okumaları

async def test_takvim_araligi_sessizce_kirpilmaz() -> None:
    # Kırpılmış bir aralık, menüsü olan günleri "menü girilmemiş" diye
    # gösterirdi — ızgaranın söylediği en pahalı yalan bu olurdu.
    service, api, _, _ = _service(calendar_max_days=31)
    sonuc = await service.calendar(date_from="2026-01-01", date_to="2026-06-30")
    assert sonuc["ok"] is False
    assert "en çok 31 gün" in sonuc["error"]
    assert api.names() == []                   # sunucuya hiç gidilmedi


async def test_takvim_ozeti_rozetlerin_yanindaki_sayilari_verir() -> None:
    # Renk tek başına anlam taşımaz (kit kuralı 7).
    service, _, _, _ = _service()
    sonuc = await service.calendar(date_from="2026-08-17", date_to="2026-08-23")
    assert sonuc["summary"]["published"] == 1
    assert sonuc["summary"]["missing"] == 1
    assert sonuc["meta"]["default_cutoff_time"] == "08:00"
    assert sonuc["meta"]["max_lookahead_days"] == 7


async def test_vitrin_kimligi_sifirken_gonderilmez() -> None:
    # Tek vitrinli kurulumda her isteğe kimlik iliştirmek, sunucudaki
    # `Location::getDefault()` ile ekranın gömülü sayısının bir gün ayrışması
    # riskini alırdı.
    service, api, _, _ = _service(location_id=0)
    await service.calendar(date_from="2026-08-17", date_to="2026-08-23")
    assert api.used("menu_calendar")[0]["location_id"] is None

    service, api, _, _ = _service(location_id=4)
    await service.calendar(date_from="2026-08-17", date_to="2026-08-23")
    assert api.used("menu_calendar")[0]["location_id"] == 4


async def test_urun_seciciye_yalniz_satistaki_urunler_gelir() -> None:
    # Satıştan kaldırılmış ürünü içeren gün yayınlanamıyor
    # (`ITEM_UNAVAILABLE`); listede göstermek, kurduğu menünün
    # yayınlanmayacağını yöneticiye ancak yayın düğmesinde söylemek olurdu.
    service, api, _, _ = _service()
    await service.products()
    assert api.used("product_picker")[0]["only_active"] is True


# ============================================================ gerekçe ve izin

async def test_kisa_gerekce_gerekce_isteyen_dort_ucta_reddedilir() -> None:
    # Arayüzde alanı zorunlu göstermek yetkilendirme değildir (K9): istemci
    # gövdeyi elle kurabilir. GEREKÇE İSTEYEN DÖRT UÇ: yayınlama, yayından
    # çekme, gün silme, kopyalama — hepsi müşteriye görünür hâle gelen ya da
    # geri alınması zor işlemler.
    #
    # SINIR DEĞİŞMEDİ: 10 karakter. Değişen tek şey NEREDE sorulduğu.
    service, api, _, _ = _service(day=dict(DRAFT_DAY))
    yayindaki, yayindaki_api, _, _ = _service()
    cagrilar = [
        service.publish("2026-08-24", reason="kısa", actor=ACTOR, dry_run=False),
        service.delete_day("2026-08-24", reason="kısa", actor=ACTOR, dry_run=False,
                           allow_remove=True),
        yayindaki.unpublish(TARIH, reason="kısa", actor=ACTOR, dry_run=False),
        yayindaki.duplicate(TARIH, target_date=_yarin(), overwrite=False, reason="kısa",
                            actor=ACTOR, dry_run=False),
    ]
    for cagri in cagrilar:
        sonuc = await cagri
        assert sonuc["ok"] is False
        assert "en az 10" in sonuc["error"]
    assert api.writes() == []
    assert yayindaki_api.writes() == []


def test_gerekce_istemeyen_metotlar_reason_parametresi_almaz() -> None:
    """Gerekçe İSTEMEYEN yedi metodun imzasında `reason` BULUNMAZ.

    Alanı imzada bırakıp görmezden gelmek daha küçük bir değişiklik olurdu ama
    sessiz bir tuzak kurardı: panel gerekçe göndermeye devam eder ve hiçbir
    yere yazılmadığını kimse fark etmezdi. Parametre yoksa yanlış çağrı
    `TypeError` verir — yanlış, ama GÜRÜLTÜLÜ.

    Aynı iddianın tersi de burada: gerekçe İSTEYEN dört metotta parametre
    duruyor ve varsayılanı YOK. Unutulan gerekçe orada da `TypeError` verir.
    """
    istemez = ("create_day", "update_day", "add_item", "update_item", "delete_item",
               "preview_stock", "apply_stock")
    for ad in istemez:
        parametreler = inspect.signature(getattr(MenuService, ad)).parameters
        assert "reason" not in parametreler, f"{ad}: gerekçe parametresi hâlâ duruyor"
        assert "actor" in parametreler, f"{ad}: aktör her yazmada kalmalıydı"

    ister = ("publish", "unpublish", "delete_day", "duplicate")
    for ad in ister:
        parametreler = inspect.signature(getattr(MenuService, ad)).parameters
        assert "reason" in parametreler, f"{ad}: gerekçe parametresi düşmüş"
        assert parametreler["reason"].default is inspect.Parameter.empty, \
            f"{ad}: gerekçe varsayılanı var — unutulduğunda sessizce boş geçerdi"


async def test_gerekcesiz_yazma_denetim_satirini_yine_acar() -> None:
    # SATIR GEREKÇEDEN BAĞIMSIZDIR. Ağ koparsa "kim neyi denedi" sorusunun
    # cevabı yalnız bu satırda kalır; satırı hiç açmamak, hızlanma uğruna izin
    # kendisini atmak olurdu. Atılan tek şey `reason` sütununun DOLULUĞUDUR.
    service, _, store, _ = _service()
    sonuc = await service.add_item(TARIH, fields={"menu_id": 55}, actor=ACTOR,
                                   dry_run=False)
    assert sonuc["ok"] is True
    satirlar = store.actions("item.create")
    assert [row["result"] for row in satirlar] == ["denendi", "ok"]
    assert {row["actor"] for row in satirlar} == {ACTOR}
    assert {row["reason"] for row in satirlar} == {""}


async def test_gerekcesiz_yazmada_gecide_de_gerekce_gitmez() -> None:
    # Servis kendi kafasından bir gerekçe uydurmaz ("panelden eklendi" gibi):
    # uydurulmuş bir gerekçe denetim izini, boş bir sütundan DAHA yanıltıcı
    # yapardı — okuyanın gerçek bir açıklama sandığı bir metin olurdu.
    service, api, _, _ = _service()
    await service.add_item(TARIH, fields={"menu_id": 55}, actor=ACTOR, dry_run=False)
    gonderilen = api.used("create_menu_item")[0]
    assert gonderilen["reason"] == ""
    assert gonderilen["actor"] == ACTOR


async def test_remove_izni_olmayan_gunu_silemez() -> None:
    # Çift kapı (K9): uç noktada da denetleniyor, burada TEKRAR. İzin kararı
    # servisin kendi elinde olmalı, çağıranın doğru uçtan geldiğine güvenmemeli.
    service, api, store, _ = _service(day=dict(DRAFT_DAY))
    sonuc = await service.delete_day("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                     dry_run=False, allow_remove=False)
    assert sonuc["ok"] is False
    assert "bld_menu.remove" in sonuc["error"]
    assert api.writes() == []
    assert store.results("day.delete") == ["engellendi"]


async def test_remove_izni_olmayan_kalemi_silemez() -> None:
    service, api, store, _ = _service()
    sonuc = await service.delete_item(TARIH, 901, actor=ACTOR,
                                      dry_run=False, allow_remove=False)
    assert sonuc["ok"] is False
    assert api.writes() == []
    assert store.results("item.delete") == ["engellendi"]


# ================================================================ gün yazma

async def test_gun_kurma_gecmis_tarihi_reddeder() -> None:
    service, api, _, _ = _service()
    sonuc = await service.create_day(date="2020-01-01", fields={}, items=[],
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert "geçmişte kaldı" in sonuc["error"]
    assert api.writes() == []


async def test_gun_kurma_ayni_urunu_iki_kez_kabul_etmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.create_day(
        date=_yarin(), fields={},
        items=[{"menu_id": 12}, {"menu_id": 12}],
        actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert "iki kez" in sonuc["error"]
    assert api.writes() == []


async def test_gun_kurulurken_gorsel_verilemez() -> None:
    # Geçidin `create_menu_day` imzasında `image_path` yok; sessizce düşürmek,
    # görseli veren çağrının sebebini hiçbir yerde okuyamadan görselsiz bir gün
    # üretmesi olurdu.
    service, api, _, _ = _service()
    sonuc = await service.create_day(date=_yarin(), fields={"image_path": "a/b.jpg"},
                                     items=[], actor=ACTOR,
                                     dry_run=False)
    assert sonuc["ok"] is False
    assert "Görsel" in sonuc["error"]
    assert api.writes() == []


async def test_paket_fiyati_sifir_reddedilir() -> None:
    service, api, _, _ = _service()
    sonuc = await service.create_day(date=_yarin(), fields={"package_price_kurus": 0},
                                     items=[], actor=ACTOR,
                                     dry_run=False)
    assert sonuc["ok"] is False
    assert "sıfırdan büyük" in sonuc["error"]
    assert api.writes() == []


async def test_kismi_guncellemede_yalniz_degisen_alan_gider() -> None:
    # Tam gövde göndermek, dokunulmamış alanları yanlışlıkla ezmek olurdu.
    #
    # GÜN GELECEKTE OLMALI: sabit `TARIH` takvim ilerledikçe geçmişte kalıyor
    # ve yayınlanmış geçmiş günün fiyat kilidi (`MenuService._price_lock`)
    # devreye girip testi konusuyla ilgisiz bir sebepten kırmızıya çeviriyor.
    # Kilit doğru çalışıyor; kırılan, tarihi çakılı bırakan testti.
    gun = _yarin()
    service, api, _, _ = _service(day={**DAY, "date": gun})
    sonuc = await service.update_day(gun, fields={"package_price_kurus": 19000},
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    gonderilen = api.used("update_menu_day")[0]
    assert gonderilen["package_price_kurus"] == 19000
    assert "title" not in gonderilen
    assert "internal_note" not in gonderilen


async def test_bos_guncelleme_reddedilir() -> None:
    # Yalnız `reason` taşıyan bir `PATCH`, hiçbir şey değiştirmeden denetim
    # izine satır yazardı.
    service, api, _, _ = _service()
    sonuc = await service.update_day(TARIH, fields={}, actor=ACTOR,
                                     dry_run=False)
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_bayrak_bosaltilamaz() -> None:
    # `null` metin alanlarında "temizle" demek ama bayrakta karşılığı yok:
    # sessizce `False` olsaydı kalem satışı kapanırdı.
    service, _, _, _ = _service()
    sonuc = await service.update_day(TARIH, fields={"components_sellable": None},
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert "boşaltılamaz" in sonuc["error"]


async def test_null_gonderilen_metin_alani_temizlenir() -> None:
    # "Dokunma" ile "boşalt" ayrımı geçide kadar korunur.
    service, api, _, _ = _service()
    sonuc = await service.update_day(TARIH, fields={"internal_note": None},
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("update_menu_day")[0]["internal_note"] is None


async def test_gecmis_yayinlanmis_gunun_fiyati_degistirilemez() -> None:
    # O gün için sipariş kapandı ve girmiş siparişler eski fiyattan; fiyatı
    # değiştirmek yalnız raporları bozardı. Sunucu 409 veriyor, buradaki kapı
    # nedeni cümleyle söylüyor (K9 — çift kapı).
    gecmis = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
    service, api, store, _ = _service(day={**DAY, "date": gecmis, "status": "published"})
    sonuc = await service.update_day(gecmis, fields={"package_price_kurus": 19000},
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "conflict"
    assert api.writes() == []
    assert store.results("day.update") == ["engellendi"]


async def test_gecmis_gunun_baslik_ve_notu_yine_de_duzeltilebilir() -> None:
    # Kilit YALNIZ fiyat alanlarındadır: yazım hatası düzeltmek meşru bir iş ve
    # tüm alanları kilitlemek, düzeltilemeyen bir kayıt bırakırdı.
    gecmis = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
    service, api, _, _ = _service(day={**DAY, "date": gecmis, "status": "published"})
    sonuc = await service.update_day(gecmis, fields={"internal_note": "Tedarikçi C"},
                                     actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    assert api.writes() == ["update_menu_day"]


async def test_urun_listesi_kirpilirsa_ekran_bunu_bilir() -> None:
    # Sessizce kırpılmış bir liste, aranan ürünün "silinmiş" sanılmasına yol
    # açardı. İki kırpma AYRI raporlanır: sunucunun taraması ve ekranın sınırı.
    service, api, _, _ = _service(product_limit=1)
    sonuc = await service.products()
    assert len(sonuc["items"]) == 1
    assert sonuc["truncated"] is True
    assert sonuc["server_truncated"] is False

    api.product_truncated = True
    sonuc = await service.products()
    assert sonuc["server_truncated"] is True


async def test_yayinlanmis_gun_silinemez() -> None:
    service, api, store, _ = _service()
    sonuc = await service.delete_day(TARIH, reason=GEREKCE, actor=ACTOR,
                                     dry_run=False, allow_remove=True)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "conflict"
    assert "Önce yayından çekin" in sonuc["error"]
    assert api.writes() == []
    assert store.results("day.delete") == ["engellendi"]


async def test_taslak_gun_silinebilir() -> None:
    service, api, store, _ = _service(day=dict(DRAFT_DAY))
    sonuc = await service.delete_day("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                     dry_run=False, allow_remove=True)
    assert sonuc["ok"] is True
    assert sonuc["dry_run"] is False
    assert api.writes() == ["delete_menu_day"]
    assert store.results("day.delete") == ["denendi", "ok"]


# ================================================================== yayın

async def test_kalemsiz_gun_yayinlanmaz() -> None:
    service, api, store, _ = _service(day={**DRAFT_DAY, "items": []})
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is False
    assert "en az bir kalem" in sonuc["error"]
    assert api.writes() == []
    assert store.results("publish") == ["engellendi"]


async def test_hicbir_sey_satilamayan_gun_yayinlanmaz() -> None:
    service, api, _, _ = _service(day={**DRAFT_DAY, "package_price_kurus": None,
                                       "components_sellable": False})
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is False
    assert "hiçbir şey satılamaz" in sonuc["error"]
    assert api.writes() == []


async def test_yayin_olay_yayinlar() -> None:
    service, _, _, bus = _service(day=dict(DRAFT_DAY))
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is True
    assert bus.names() == ["bld_menu.day_published"]
    assert bus.events[0][1]["date"] == "2026-08-24"
    assert bus.events[0][1]["itemCount"] == 2


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    # BLD'de hiçbir şey değişmedi; dinleyicileri "gün yayınlandı" diye
    # uyandırmak yalan olurdu.
    service, _, store, bus = _service(day=dict(DRAFT_DAY))
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=True)
    assert sonuc["ok"] is True
    assert sonuc["dry_run"] is True
    assert bus.events == []
    assert store.results("publish") == ["denendi", "dry_run"]


async def test_sunucu_provaya_cevirirse_ekran_yapildi_demez() -> None:
    # Bir kurulum geçidin varsayılanını açık bırakırsa yanıt `dry_run: true`
    # taşır. Ekran bunu YANITTAN okur, istekten değil.
    service, api, _, bus = _service(day=dict(DRAFT_DAY))

    async def prova(date: str, **kwargs: Any) -> dict[str, Any]:
        api.calls.append(("publish_menu_day", (date,), kwargs))
        return {"ok": True, "dry_run": True, "audit_id": 7, "would": {"date": date}}

    api.publish_menu_day = prova  # type: ignore[method-assign]
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["dry_run"] is True
    assert bus.events == []


async def test_yayinda_olmayan_gun_taslaga_cekilemez() -> None:
    service, api, _, _ = _service(day=dict(DRAFT_DAY))
    sonuc = await service.unpublish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                    dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "conflict"
    assert api.writes() == []


async def test_yayindan_cekme_olay_yayinlar() -> None:
    service, _, _, bus = _service()
    sonuc = await service.unpublish(TARIH, reason=GEREKCE, actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    assert bus.names() == ["bld_menu.day_unpublished"]


async def test_dinleyici_patlarsa_is_basarili_kalir() -> None:
    # Gün BLD'de yayınlanmıştır; dinleyicinin patlaması onu geri almaz (K7).
    service, _, _, bus = _service(day=dict(DRAFT_DAY))
    bus.fail = True
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is True


# ================================================================= kalemler

async def test_ayni_urun_iki_kez_menuye_eklenmez() -> None:
    # Tekil indeks var (`veykemtu_daily_menu_item_essiz`); buradaki kapı
    # "Tavuk Sote zaten menüde" cümlesini kurar.
    service, api, store, _ = _service()
    sonuc = await service.add_item(TARIH, fields={"menu_id": 27}, actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "conflict"
    assert api.writes() == []
    assert store.results("item.create") == ["engellendi"]


async def test_yeni_urun_eklenebilir() -> None:
    service, api, store, _ = _service()
    sonuc = await service.add_item(TARIH, fields={"menu_id": 55, "quantity": 1,
                                                  "sellable_alone": False},
                                   actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("create_menu_item")[0]["menu_id"] == 55
    # `sort_order` GÖNDERİLMEDİ: sunucu mevcut en büyüğe 10 ekliyor ve `null`
    # göndermek bu davranışı tetiklemeyebilirdi.
    assert "sort_order" not in api.used("create_menu_item")[0]
    assert store.results("item.create") == ["denendi", "ok"]


async def test_kalem_guncellemede_urun_degistirilemez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.update_item(TARIH, 901, fields={"menu_id": 27},
                                      actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert "silip yenisini eklemektir" in sonuc["error"]
    assert api.writes() == []


async def test_olmayan_kalem_guncellenmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.update_item(TARIH, 999, fields={"quantity": 2},
                                      actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "not_found"
    assert api.writes() == []


async def test_kalem_fiyati_sifir_gecerlidir() -> None:
    # Paket içinde satılan ekmek/ayran fiyatsız olabilir; paket fiyatının
    # aksine burada sıfır bir anlam taşır.
    service, api, _, _ = _service()
    sonuc = await service.update_item(TARIH, 901, fields={"price_override_kurus": 0},
                                      actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("update_menu_item")[0]["price_override_kurus"] == 0


async def test_kalem_silme_denetim_izine_adi_yazar() -> None:
    service, _, store, _ = _service()
    sonuc = await service.delete_item(TARIH, 902, actor=ACTOR,
                                      dry_run=False, allow_remove=True)
    assert sonuc["ok"] is True
    detay = json.loads(store.actions("item.delete")[0]["detail"])
    assert detay["name"] == "Tavuk Sote"
    assert detay["menu_id"] == 27


# ===================================================================== stok

async def test_stok_onizlemesi_uyarilari_ve_jeton_dondurur() -> None:
    # Kuru provanın ASIL İŞİ bu: yönetici tavanı yazmadan önce kaç siparişin
    # altında kaldığını görür.
    service, api, store, _ = _service()
    sonuc = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    assert sonuc["ok"] is True
    assert sonuc["dry_run"] is True
    assert sonuc["token"]
    assert sonuc["warnings"][0]["code"] == "capacity_below_sold"
    assert sonuc["warnings"][0]["label"] == "Tavan satılmışın altında"
    # Geçide KURU PROVA olarak gitti.
    assert api.used("set_menu_stock")[0]["dry_run"] is True
    assert store.previews[sonuc["token"]]["status"] == "dry_run"


async def test_eksik_tavan_tablosu_onizlemede_reddedilir() -> None:
    # TAM LİSTEDİR: gönderilmeyen kalemin tavanı kalkar.
    service, api, _, _ = _service()
    sonuc = await service.preview_stock(TARIH, capacity_total=120,
                                        items=[{"item_id": 901, "capacity": None}],
                                        actor=ACTOR)
    assert sonuc["ok"] is False
    assert "902" in sonuc["error"]
    assert api.writes() == []


async def test_stok_jetonsuz_uygulanamaz() -> None:
    service, api, _, _ = _service()
    sonuc = await service.apply_stock(TARIH, token="", actor=ACTOR)
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_onizlenen_tablo_jetonla_uygulanir() -> None:
    service, api, store, bus = _service()
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    sonuc = await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)
    assert sonuc["ok"] is True
    assert sonuc["dry_run"] is False
    # İkinci çağrı GERÇEK yazma olarak gitti ve tablo jetondan geldi.
    yazmalar = api.used("set_menu_stock")
    assert [call["dry_run"] for call in yazmalar] == [True, False]
    assert yazmalar[1]["capacity_total"] == 120
    assert yazmalar[1]["items"] == [{"item_id": 901, "capacity": None},
                                    {"item_id": 902, "capacity": 60}]
    assert store.previews[onizleme["token"]]["status"] == "applied"
    assert bus.names() == ["bld_menu.stock_changed"]


async def test_jeton_bir_kez_kullanilir() -> None:
    # Uygulanmış bir jeton yeniden kullanılabilseydi, aynı tablo ikinci kez ve
    # bu sefer eski satış sayılarıyla yazılabilirdi.
    service, _, _, _ = _service()
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)
    ikinci = await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)
    assert ikinci["ok"] is False
    assert "süresi doldu" in ikinci["error"]


async def test_arada_satis_degistiyse_uygulama_reddedilir() -> None:
    # Yarım saat önce görülen sayılara bakarak tavan yazmak, doğru kararı
    # yanlış veriye dayandırmaktır.
    service, api, store, bus = _service()
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)

    api.stock_payload = {**api.stock_payload,
                         "day": {**api.stock_payload["day"], "sold": 92}}
    sonuc = await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)
    assert sonuc["ok"] is False
    assert sonuc["code"] == "conflict"
    assert "86 → 92" in sonuc["error"]
    # GERÇEK yazma HİÇ gitmedi; yalnız önizlemenin kuru provası var.
    assert [call["dry_run"] for call in api.used("set_menu_stock")] == [True]
    assert bus.events == []
    assert "engellendi" in store.results("stock")


async def test_suresi_dolmus_onizleme_uygulanmaz() -> None:
    service, _, store, _ = _service(stock_preview_ttl_minutes=1)
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    eski = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    store.previews[onizleme["token"]]["created_at"] = eski.replace("+00:00", "Z")

    sonuc = await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)
    assert sonuc["ok"] is False
    assert "süresi doldu" in sonuc["error"]


async def test_baska_gunun_jetonu_kabul_edilmez() -> None:
    service, _, _, _ = _service()
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    sonuc = await service.apply_stock("2026-08-18", token=onizleme["token"],
                                      actor=ACTOR)
    assert sonuc["ok"] is False
    assert "başka bir güne" in sonuc["error"]


# ================================================================ kopyalama

async def test_ayni_gune_kopyalanmaz() -> None:
    service, api, _, _ = _service()
    sonuc = await service.duplicate(TARIH, target_date=TARIH, overwrite=False,
                                    reason=GEREKCE, actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_kalemsiz_gun_kopyalanmaz() -> None:
    service, api, _, _ = _service(day={**DAY, "items": []})
    sonuc = await service.duplicate(TARIH, target_date=_yarin(), overwrite=False,
                                    reason=GEREKCE, actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is False
    assert "kalem yok" in sonuc["error"]
    assert api.writes() == []


async def test_kopyalama_denetim_hedefi_hedef_gundur() -> None:
    # "Bu gün nereden geldi" sorusu hedef tarihle sorulur; kaynak `detail`
    # içinde durur.
    service, _, store, _ = _service()
    hedef = _yarin()
    sonuc = await service.duplicate(TARIH, target_date=hedef, overwrite=False,
                                    reason=GEREKCE, actor=ACTOR, dry_run=False)
    assert sonuc["ok"] is True
    satir = store.actions("duplicate")[-1]
    assert satir["target_date"] == hedef
    assert json.loads(satir["detail"])["source_date"] == TARIH


# ============================================================== denetim izi

async def test_denendi_izi_gecit_cagrisindan_once_duser() -> None:
    # "Ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında tek kanıttır.
    service, api, store, _ = _service(day=dict(DRAFT_DAY))
    api.fail.add("publish_menu_day")
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is False
    assert store.results("publish") == ["denendi", "hata"]


async def test_iz_yazilamazsa_is_durmaz() -> None:
    # Denetim satırı yazılamadı diye yayını engellemek, bir kayıt sorununu bir
    # satış sorununa çevirmek olurdu (K7).
    service, api, store, _ = _service(day=dict(DRAFT_DAY))
    store.broken = True
    sonuc = await service.publish("2026-08-24", reason=GEREKCE, actor=ACTOR,
                                  dry_run=False)
    assert sonuc["ok"] is True
    assert api.writes() == ["publish_menu_day"]


async def test_aktor_izde_saklanir() -> None:
    service, _, store, _ = _service()
    await service.update_day(TARIH, fields={"title": "Yeni"}, actor=ACTOR, dry_run=False)
    assert {row["actor"] for row in store.actions("day.update")} == {ACTOR}


# ================================================= kuru prova AÇIKÇA geçilir

async def test_her_yazmada_dry_run_acikca_gecilir() -> None:
    """Geçidin varsayılanına ASLA güvenilmez.

    `config/local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor.
    Bayrağı atlayan bir modül hiçbir şey yazmadan `{"ok": true}` alır ve ekran
    "kaydedildi" der; "kaydedildi" ile "sessizce atıldı" ayırt edilemez hâle
    gelir. Bu test on yazma metodunun onunu birden tarar — yeni bir metot
    eklenip bayrağı unutulursa burada düşer.
    """
    service, api, _, _ = _service()
    hedef = _yarin()

    await service.create_day(date=hedef, fields={"title": "Deneme"}, items=[],
                             actor=ACTOR, dry_run=False)
    await service.update_day(TARIH, fields={"title": "Yeni"}, actor=ACTOR, dry_run=False)
    await service.unpublish(TARIH, reason=GEREKCE, actor=ACTOR, dry_run=False)
    await service.add_item(TARIH, fields={"menu_id": 55}, actor=ACTOR,
                           dry_run=False)
    await service.update_item(TARIH, 901, fields={"quantity": 2}, actor=ACTOR, dry_run=False)
    await service.delete_item(TARIH, 902, actor=ACTOR, dry_run=False,
                              allow_remove=True)
    await service.duplicate(TARIH, target_date=hedef, overwrite=False, reason=GEREKCE,
                            actor=ACTOR, dry_run=False)
    onizleme = await service.preview_stock(
        TARIH, capacity_total=120,
        items=[{"item_id": 901, "capacity": None}, {"item_id": 902, "capacity": 60}],
        actor=ACTOR)
    await service.apply_stock(TARIH, token=onizleme["token"], actor=ACTOR)

    taslak, taslak_api, _, _ = _service(day=dict(DRAFT_DAY))
    await taslak.publish("2026-08-24", reason=GEREKCE, actor=ACTOR, dry_run=False)
    await taslak.delete_day("2026-08-24", reason=GEREKCE, actor=ACTOR, dry_run=False,
                            allow_remove=True)

    yazan = set(api.writes()) | {"publish_menu_day", "delete_menu_day"}
    assert yazan == {"create_menu_day", "update_menu_day", "delete_menu_day",
                     "publish_menu_day", "unpublish_menu_day", "create_menu_item",
                     "update_menu_item", "delete_menu_item", "set_menu_stock",
                     "duplicate_menu_day"}, "yazan metot listesi ayrıştı"

    for ad, _, kwargs in [*api.calls, *taslak_api.calls]:
        if ad in yazan:
            assert "dry_run" in kwargs, f"{ad} `dry_run` geçirmemiş"
            assert isinstance(kwargs["dry_run"], bool), \
                f"{ad} `dry_run=None` geçirmiş — geçidin varsayılanına düşerdi"


# ============================================= satışa açma (auto_open_on_price)


def _satilamayan_gun() -> dict[str, Any]:
    """Paket fiyatı girilmiş, kalemleri var, ama HİÇBİRİ zorunlu değil.

    Kontrol Merkezi'nin hızlı ekleme akışının ürettiği gündü: BLD paketi
    `no_components` diye satışa açmıyor, sitede paket kartı hiç çizilmiyor ve
    ekranda tek bir işaret yoktu.
    """
    return {
        **DRAFT_DAY,
        "items": [{**item, "is_required": False} for item in DRAFT_DAY["items"]],
    }


async def test_satisa_acma_eksik_zorunlu_isaretleri_kurar_ve_yayinlar() -> None:
    service, api, _, bus = _service(day=_satilamayan_gun())

    sonuc = await service.open_sale("2026-08-24", actor=ACTOR, dry_run=False)

    assert sonuc["ok"] is True
    # İKİ KALEMİN İKİSİ DE zorunlu yapıldı: paketin içi "en az bir tanesi"
    # değil, o günün yemekleridir.
    assert sonuc["required_items"] == 2
    assert sonuc["published"] is True
    # Kalem kimliği KONUMSAL geçiyor (geçidin imzası öyle), alanlar anahtarlı.
    assert api.args_of("update_menu_item") == [("2026-08-24", 901), ("2026-08-24", 902)]
    assert api.used("update_menu_item") == [
        {"reason": "", "actor": ACTOR, "dry_run": False, "is_required": True},
        {"reason": "", "actor": ACTOR, "dry_run": False, "is_required": True},
    ]
    # Gerekçe AYARIN ADINI taşıyor: denetim izini okuyan kişi, kapatılacak
    # düğmenin adını da okumuş olur.
    yayin = api.used("publish_menu_day")[0]
    assert "auto_open_on_price" in yayin["reason"]
    assert yayin["actor"] == ACTOR
    assert bus.events[-1][0] == "bld_menu.day_published"


async def test_satisa_acma_zorunlu_kalem_varsa_kalemlere_dokunmaz() -> None:
    # Gün yalnız taslakta kalmış olabilir; o zaman yapılacak tek iş yayındır.
    service, api, _, _ = _service(day=dict(DRAFT_DAY))

    sonuc = await service.open_sale("2026-08-24", actor=ACTOR, dry_run=False)

    assert sonuc["required_items"] == 0
    assert sonuc["published"] is True
    assert api.used("update_menu_item") == []


async def test_satisa_acma_yayindaki_gunu_tekrar_yayinlamaz() -> None:
    # Yeniden yayınlamak satılmış porsiyonları sıfırlamaz ama denetim izine
    # anlamsız bir satır yazar ve olayı ikinci kez duyururdu.
    service, api, _, bus = _service()

    sonuc = await service.open_sale(TARIH, actor=ACTOR, dry_run=False)

    assert sonuc["ok"] is True
    assert sonuc["already"] is True
    assert sonuc["published"] is False
    assert api.used("publish_menu_day") == []
    assert bus.events == []


async def test_satisa_acma_paket_fiyati_olmayan_gune_dokunmaz() -> None:
    # Paketsiz gün bir arıza değil: kalemleri tek tek satılıyordur. Onu
    # kendiliğinden yayına itmek, kullanıcının yazmadığı bir kararı yazmak olurdu.
    service, api, _, _ = _service(day={**DRAFT_DAY, "package_price_kurus": None})

    sonuc = await service.open_sale("2026-08-24", actor=ACTOR, dry_run=False)

    assert sonuc["ok"] is False
    assert "paket fiyatı" in sonuc["error"].lower()
    assert api.writes() == []


async def test_satisa_acma_kalemsiz_gunu_yayinlamaz() -> None:
    # Boş bir menü kartı yayınlamak müşteriye hiçbir şey satmaz; cümle
    # `publish_error()`ten geliyor ve olduğu gibi taşınıyor.
    service, api, _, _ = _service(day={**DRAFT_DAY, "items": []})

    sonuc = await service.open_sale("2026-08-24", actor=ACTOR, dry_run=False)

    # `ok: True` — hata değil, yarım kalmış bir iş. Neyin eksik olduğunu `note` söyler.
    assert sonuc["ok"] is True
    assert sonuc["published"] is False
    assert "kalem" in sonuc["note"]
    assert api.used("publish_menu_day") == []


async def test_satisa_acma_kuru_provada_hicbir_sey_yazmaz() -> None:
    service, api, _, bus = _service(day=_satilamayan_gun())

    sonuc = await service.open_sale("2026-08-24", actor=ACTOR, dry_run=True)

    assert sonuc["dry_run"] is True
    # Kalemlere GERÇEK yazma gitmedi; ne yapılacağı sayıyla söylendi.
    assert api.used("update_menu_item") == []
    assert sonuc["required_items"] == 2
    assert api.used("publish_menu_day")[0]["dry_run"] is True
    # Kuru provada olay YAYINLANMAZ: BLD'de hiçbir şey değişmedi.
    assert bus.events == []


def test_ayar_varsayilan_acik_ve_sozlesmede_gorunur() -> None:
    # Panel bu bayrağa bakarak fiyat kaydından sonra ucu kendiliğinden çağırıyor;
    # bayrağın kopyası panelde tutulmuyor.
    acik, _, _, _ = _service()
    assert acik.contract()["auto_open_on_price"] is True

    kapali, _, _, _ = _service(auto_open_on_price=False)
    assert kapali.contract()["auto_open_on_price"] is False


async def test_toplu_satisa_acma_yalniz_uygun_gunlere_dokunur() -> None:
    """Aday üç şartı BİRDEN taşır: menüsü var, paket fiyatı var, günü geçmemiş.

    Takvimde dördü de yanlış olan satırlar duruyor ve hiçbirine dokunulmamalı:
    fiyatsız gün kalemlerini tek tek satıyordur, menüsüz gün zaten yoktur ve
    geçmiş güne yayın kimseye sipariş verdirmez.
    """
    yarin = _yarin()
    obur = (datetime.now(UTC) + timedelta(days=4)).strftime("%Y-%m-%d")
    dun = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")

    service, api, _, _ = _service(day=dict(DRAFT_DAY))
    api.day_rows = {
        yarin: {**DRAFT_DAY, "date": yarin,
                "items": [{**item, "is_required": False} for item in DRAFT_DAY["items"]]},
        obur: {**DRAFT_DAY, "date": obur},
        dun: {**DRAFT_DAY, "date": dun},
    }
    api.calendar_rows = [
        {"date": yarin, "id": 1, "status": "draft", "package_price_kurus": 25000,
         "item_count": 2},
        {"date": obur, "id": 2, "status": "draft", "package_price_kurus": 25000,
         "item_count": 2},
        # Fiyatsız: dokunulmaz.
        {"date": (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d"),
         "id": 3, "status": "draft", "package_price_kurus": None, "item_count": 2},
        # Menüsüz gün.
        {"date": (datetime.now(UTC) + timedelta(days=6)).strftime("%Y-%m-%d"),
         "id": None, "status": None, "package_price_kurus": None, "item_count": 0},
        # Geçmiş gün: fiyatı olsa bile denenmez.
        {"date": dun, "id": 4, "status": "draft", "package_price_kurus": 25000,
         "item_count": 2},
    ]

    sonuc = await service.open_sale_range(date_from=_yarin(), date_to=_yarin(),
                                          actor=ACTOR, dry_run=False)

    assert sonuc["ok"] is True
    assert sonuc["totals"]["candidates"] == 2
    assert sonuc["totals"]["published"] == 2
    # Yalnız zorunlu işareti eksik olan günün kalemlerine dokunuldu.
    assert sonuc["totals"]["required_items"] == 2
    assert [row["date"] for row in sonuc["days"]] == [yarin, obur]
    # Geçmiş gün HİÇ OKUNMADI bile: aday listesine girmediği için ona dair tek
    # bir istek çıkmadı.
    assert dun not in [args[0] for args in api.args_of("menu_day")]
    assert [args[0] for args in api.args_of("publish_menu_day")] == [yarin, obur]


async def test_toplu_satisa_acma_kuru_provada_hicbir_sey_yazmaz() -> None:
    yarin = _yarin()
    service, api, _, _ = _service(day=dict(DRAFT_DAY))
    api.day_rows = {yarin: {**DRAFT_DAY, "date": yarin}}
    api.calendar_rows = [{"date": yarin, "id": 1, "status": "draft",
                          "package_price_kurus": 25000, "item_count": 2}]

    sonuc = await service.open_sale_range(date_from=yarin, date_to=yarin,
                                          actor=ACTOR, dry_run=True)

    assert sonuc["dry_run"] is True
    assert sonuc["totals"]["published"] == 1          # ne OLACAĞINI söyler
    assert api.used("publish_menu_day")[0]["dry_run"] is True


async def test_toplu_satisa_acma_araligi_sozlesme_tavaniyla_sinirli() -> None:
    # Aşan aralığı sunucu 422 ile geri çevirirdi; ekran nedenini cümleyle söyler
    # (K9 — çift kapı).
    service, api, _, _ = _service(calendar_max_days=7)
    sonuc = await service.open_sale_range(date_from="2026-08-01", date_to="2026-09-30",
                                          actor=ACTOR, dry_run=True)
    assert sonuc["ok"] is False
    assert "7 gün" in sonuc["error"]
    assert api.calls == []
