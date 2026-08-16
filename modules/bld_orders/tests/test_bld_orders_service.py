"""İş kuralları — yazma zinciri, K7 dayanıklılığı, izin kapısı, dışa aktarım.

Testler AĞA ÇIKMAZ: `FakeApi` geçidin testlik yüzüdür ve metot adları
`bld_api/backend/client.py` ile birebir aynıdır.
"""

from __future__ import annotations

from typing import Any

from bld_orders_fakes import (
    ORDER_DETAIL,
    FakeApi,
    FakeBus,
    FakeStore,
    make_service,
)

GEREKCE = "Müşteri telefonla iki porsiyon azalttı"
KALEMLER = [{"menu_id": 88, "quantity": 10, "option_value_ids": [], "note": None}]


# ================================================================== okuma

async def test_ozet_aga_cikmaz() -> None:
    # Süzgeç şeridi ve etiketler geçit düşükken de çizilebilmeli (K7).
    api = FakeApi()
    api.fail = {"order_list", "order_detail"}
    sonuc = await make_service(api=api).overview()
    assert sonuc["ok"] is True
    assert api.names() == []
    assert sonuc["contract"]["reason"]["max"] == 160


async def test_liste_sayfa_ve_sayac_dondurur() -> None:
    sonuc = await make_service().orders(page=2, per_page=50)
    assert sonuc["ok"] is True
    assert sonuc["connected"] is True
    assert sonuc["items"][0]["status_label"] == "Hazırlanıyor"
    assert sonuc["meta"]["page"] == 2
    # Sayaçlar YALNIZ bu sayfayı sayar; panel bunu yazıyla söyler.
    assert sonuc["page_counts"]["hazirlaniyor"] == 1
    assert sonuc["page_counts"]["iptal"] == 0


async def test_gecit_duserse_liste_ok_true_connected_false_doner() -> None:
    # K7: uç 200 verir ve panel çökmez. `ok` UCUN SAĞLIĞINI anlatır; ayrımı
    # `connected` taşır. Yalnız `ok`a bakan bir panel "sipariş yok" derdi.
    api = FakeApi()
    api.fail = {"order_list"}
    sonuc = await make_service(api=api).orders()
    assert sonuc["ok"] is True
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    assert sonuc["error"]


async def test_bozuk_suzgec_baglanti_hatasi_gibi_gosterilmez() -> None:
    sonuc = await make_service().orders(status="hazrilaniyor")
    assert sonuc["ok"] is False
    assert sonuc["connected"] is None
    assert "hazrilaniyor" in sonuc["error"]


async def test_bos_suzgecler_istekten_dusurulur() -> None:
    api = FakeApi()
    await make_service(api=api).orders(q="acme")
    cagri = api.used("order_list")[0]
    assert cagri["q"] == "acme"
    # Boş liste `None` gider ve geçit onu sorgu dizesinden tümüyle düşürür;
    # `status=` göndermek listeyi sessizce boşaltabilirdi.
    assert cagri["status"] is None
    assert cagri["customer_id"] is None


async def test_ters_tarih_araligi_reddedilir() -> None:
    sonuc = await make_service().orders(date_from="2026-08-20", date_to="2026-08-10")
    assert sonuc["ok"] is False
    assert "sonra olamaz" in sonuc["error"]


async def test_siparis_ayrintisi_geri_alma_penceresini_yuzeye_cikarir() -> None:
    sonuc = await make_service().order(8421)
    assert sonuc["ok"] is True
    # Sözleşme `can_undo` alanını saymıyor: ekran "bilinmiyor" der ve düğmeyi
    # hiç çizmez.
    assert sonuc["undo"]["known"] is False

    api = FakeApi(detail={**ORDER_DETAIL, "can_undo": True,
                          "undo_until": "2026-08-16T09:01:00Z"})
    sonuc = await make_service(api=api).order(8421)
    assert sonuc["undo"]["can_undo"] is True
    assert sonuc["undo"]["seconds_left"] == 60


async def test_fatura_yoksa_hata_degil_eksiktir() -> None:
    # Siparişlerin çoğunda fatura belgesi hiç oluşturulmaz; kırmızı bir hata
    # kutusu olağan bir durumu arıza gibi gösterirdi.
    api = FakeApi()
    api.fail = {"order_invoice"}
    api.fail_code = "not_found"
    sonuc = await make_service(api=api).order_invoice(8421)
    assert sonuc["ok"] is True
    assert sonuc["connected"] is True
    assert sonuc["missing"] is True
    assert sonuc["error"] == ""


async def test_fatura_ucu_duserse_baglanti_sorunu_olarak_gorunur() -> None:
    api = FakeApi()
    api.fail = {"order_invoice"}
    api.fail_code = "transport"
    sonuc = await make_service(api=api).order_invoice(8421)
    assert sonuc["connected"] is False
    assert sonuc["missing"] is False
    assert sonuc["error"]


# ============================================================ revizyon yazma

async def test_revizyon_once_denendi_izini_yazar() -> None:
    # Ağ koparsa geriye YALNIZ bu satır kalır: "kim neyi denedi" sorusunun
    # cevabı uzak kayıtta yok, çünkü o yalnız sunucuya ULAŞAN isteği bilir.
    store = FakeStore()
    await make_service(store=store).create_revision(
        8421, items=KALEMLER, reason=GEREKCE, actor="Ayşe Yılmaz", dry_run=False)
    assert store.results("order.revise") == ["denendi", "ok"]


async def test_revizyon_kalem_listesini_oldugu_gibi_gecirir() -> None:
    api = FakeApi()
    kalemler = [{"menu_id": 88, "quantity": 10, "option_value_ids": [4], "note": "az tuz"}]
    await make_service(api=api).create_revision(
        8421, items=kalemler, reason=GEREKCE, actor="Ayşe", dry_run=False)
    gonderilen = api.used("revise_order")[0]["items"]
    # `option_value_ids` düşseydi "ekstra peynir" silinir, sipariş ucuzlar ve
    # mutfak yanlış yemeği yapardı.
    assert gonderilen[0]["option_value_ids"] == [4]
    assert gonderilen[0]["note"] == "az tuz"


async def test_revizyon_yazmadan_once_taze_okur() -> None:
    # Ekranda on dakika önce açılmış bir sepetle yazmak, aradan kasadan yapılmış
    # bir değişikliği geri alırdı.
    api = FakeApi()
    await make_service(api=api).create_revision(
        8421, items=KALEMLER, reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert api.names() == ["order_detail", "revise_order"]


async def test_duzenlenemeyen_siparise_revizyon_yazilmaz() -> None:
    api = FakeApi(detail={**ORDER_DETAIL, "status": "iptal", "editable": False,
                          "not_editable_reason": "cancelled"})
    store = FakeStore()
    sonuc = await make_service(api=api, store=store).create_revision(
        8421, items=KALEMLER, reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert "İptal edilmiş" in sonuc["error"]
    # Ağ turu da denetim satırı da yok: anlamsız bir deneme kaydı, izi
    # gürültüyle doldururdu.
    assert api.names() == ["order_detail"]
    assert store.audit == []


async def test_bos_kalem_listesi_gecide_hic_gitmez() -> None:
    api = FakeApi()
    sonuc = await make_service(api=api).create_revision(
        8421, items=[], reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_kisa_gerekce_gecide_hic_gitmez() -> None:
    # Arayüzde alanı zorunlu göstermek yetkilendirme değildir (K9).
    api = FakeApi()
    sonuc = await make_service(api=api).create_revision(
        8421, items=KALEMLER, reason="kısa", actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert api.names() == []


# ============================================================= durum yazma

async def test_durum_gecisi_olayi_yayinlar() -> None:
    bus = FakeBus()
    sonuc = await make_service(bus=bus).set_status(
        8421, status="hazir", reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is True
    # Ad `bld_kds`inkinden AYRI: aynı adı ikinci kez yayınlamak, dinleyicinin
    # olayın nereden geldiğini ayırt edememesi olurdu.
    assert bus.names() == ["bld_orders.order_status_changed"]
    assert bus.events[0][1]["to"] == "hazir"


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    # BLD'de hiçbir şey değişmedi; dinleyicileri uyandırmak yalan olurdu.
    bus = FakeBus()
    store = FakeStore()
    sonuc = await make_service(bus=bus, store=store).set_status(
        8421, status="hazir", reason=GEREKCE, actor="Ayşe", dry_run=True)
    assert sonuc["dry_run"] is True
    assert sonuc["announced"] is False
    assert bus.events == []
    assert store.results("order.status") == ["denendi", "dry_run"]


async def test_kuru_prova_bayragi_gecide_ACIKCA_gecer() -> None:
    # Geçidin varsayılanına GÜVENİLMEZ: `config/local.yaml` git dışıdır ve
    # orada `dry_run_default: true` yazıyor olabilir. Bayrağı atlayan bir çağrı
    # hiçbir şey yazmadan `{"ok": true}` alır ve ekran "kaydedildi" der.
    api = FakeApi()
    servis = make_service(api=api)
    await servis.set_status(8421, status="hazir", reason=GEREKCE, actor="Ayşe",
                            dry_run=False)
    await servis.create_revision(8421, items=KALEMLER, reason=GEREKCE, actor="Ayşe",
                                 dry_run=True)
    await servis.cancel(8421, reason=GEREKCE, actor="Ayşe", dry_run=False,
                        allow_cancel=True)
    assert api.used("change_order_status")[0]["dry_run"] is False
    assert api.used("revise_order")[0]["dry_run"] is True
    assert api.used("cancel_order")[0]["dry_run"] is False


async def test_bayrak_verilmezse_modul_varsayilani_uygulanir() -> None:
    api = FakeApi()
    servis = make_service(api=api, config={"dry_run_default": True})
    await servis.set_status(8421, status="hazir", reason=GEREKCE, actor="Ayşe")
    assert api.used("change_order_status")[0]["dry_run"] is True


async def test_iptal_durum_ucundan_gecmez() -> None:
    # `bld_orders.cancel` iznini kâğıt üstünde bırakırdı.
    api = FakeApi()
    sonuc = await make_service(api=api).set_status(
        8421, status="iptal", reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert "ayrı" in sonuc["error"]
    assert api.names() == []


async def test_taninmayan_durum_kodu_reddedilir() -> None:
    api = FakeApi()
    sonuc = await make_service(api=api).set_status(
        8421, status="pisiyor", reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_sunucu_gecisi_reddederse_mesaja_baglam_eklenir() -> None:
    # Geçit `error.details` taşımıyor; ekranın bildiği iki değer (istek anındaki
    # durum + hedef) cümleye eklenir. Matrisin kopyası HÂLÂ yok.
    api = FakeApi()
    api.fail = {"change_order_status"}
    api.fail_code = "validation"
    store = FakeStore()
    sonuc = await make_service(api=api, store=store).set_status(
        8421, status="yolda", reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert "Hazırlanıyor" in sonuc["error"]
    assert "Yolda" in sonuc["error"]
    assert store.results("order.status") == ["denendi", "hata"]


# ================================================================== iptal

async def test_izinsiz_iptal_gecide_gitmez_ama_ize_duser() -> None:
    # ÇİFT KAPI (K9): uç noktada da denetleniyor. "Engellendi" satırı, denemenin
    # kendisinin de bir bilgi olduğunu söyler.
    api = FakeApi()
    store = FakeStore()
    sonuc = await make_service(api=api, store=store).cancel(
        8421, reason=GEREKCE, actor="Ayşe", dry_run=False, allow_cancel=False)
    assert sonuc["ok"] is False
    assert api.names() == []
    assert store.results("order.cancel") == ["engellendi"]


async def test_iptal_stok_iadesini_ve_iade_tutarini_yuzeye_cikarir() -> None:
    bus = FakeBus()
    sonuc = await make_service(bus=bus).cancel(
        8421, reason=GEREKCE, actor="Ayşe", dry_run=False, allow_cancel=True)
    assert sonuc["ok"] is True
    assert sonuc["data"]["refund_kurus"] == 216000
    # İptalin en önemli yan etkisi: o kadar sipariş yeniden alınabilir hâle
    # gelir. Ekran göstermezse yönetici "neden birden 12 yer açıldı" diye sorar.
    assert sonuc["data"]["stock_released"]["day"] == 12
    assert bus.names() == ["bld_orders.order_cancelled"]
    assert bus.events[0][1]["refundKurus"] == 216000


async def test_iptal_ayri_olay_yayinlar_durum_olayi_degil() -> None:
    # Ayrımı gövdeye bakarak yapmak zorunda kalan bir dinleyici, iadeyi
    # kaçırdığında sessizce yanlış çalışırdı.
    bus = FakeBus()
    await make_service(bus=bus).cancel(8421, reason=GEREKCE, actor="Ayşe",
                                       dry_run=False, allow_cancel=True)
    assert "bld_orders.order_status_changed" not in bus.names()


async def test_iptal_iade_ve_bildirim_bayraklarini_oldugu_gibi_gecirir() -> None:
    api = FakeApi()
    await make_service(api=api).cancel(8421, reason=GEREKCE, actor="Ayşe", refund=False,
                                       notify_customer=False, dry_run=False,
                                       allow_cancel=True)
    cagri = api.used("cancel_order")[0]
    assert cagri["refund"] is False
    assert cagri["notify_customer"] is False


async def test_iptal_cakismasi_tazeleme_cumlesi_ekler() -> None:
    api = FakeApi()
    api.fail = {"cancel_order"}
    api.fail_code = "conflict"
    sonuc = await make_service(api=api).cancel(8421, reason=GEREKCE, actor="Ayşe",
                                               dry_run=False, allow_cancel=True)
    assert sonuc["ok"] is False
    assert "tazeleyin" in sonuc["error"]


async def test_dinleyici_patlarsa_is_basarili_kalir() -> None:
    # Sipariş BLD'de iptal edilmiştir; dinleyicinin patlaması onu geri getirmez.
    bus = FakeBus()
    bus.fail = True
    sonuc = await make_service(bus=bus).cancel(8421, reason=GEREKCE, actor="Ayşe",
                                               dry_run=False, allow_cancel=True)
    assert sonuc["ok"] is True


async def test_iz_yazilamazsa_is_durmaz() -> None:
    # K7: denetim satırı yazılamadı diye siparişin durumu değiştirilemez hâle
    # gelseydi, bir disk sorunu satışı durdururdu.
    store = FakeStore()
    store.broken = True
    sonuc = await make_service(store=store).set_status(
        8421, status="hazir", reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is True


# ============================================================ dışa aktarım

async def test_disa_aktarim_baytlari_oldugu_gibi_yazar(tmp_path: Any) -> None:
    # Dosya UTF-8 BOM ile başlıyor; metne çevirip yeniden kodlamak Excel'de
    # "ğ" yerine kutu gösterirdi.
    sonuc = await make_service(export_dir=tmp_path).export(
        actor="Ayşe", date_from="2026-08-01", date_to="2026-08-16")
    assert sonuc["ok"] is True
    yazilan = (tmp_path / sonuc["name"]).read_bytes()
    assert yazilan.startswith(b"\xef\xbb\xbf")


async def test_disa_aktarim_dosyasi_yalniz_kullaniciya_okunur(tmp_path: Any) -> None:
    # Dosya kişisel veri taşır (ad, telefon): 0600.
    sonuc = await make_service(export_dir=tmp_path).export(actor="Ayşe")
    mode = (tmp_path / sonuc["name"]).stat().st_mode & 0o777
    assert mode == 0o600


async def test_sunucunun_verdigi_dosya_adi_klasor_disina_yazdirmaz(tmp_path: Any) -> None:
    # `Content-Disposition` UZAKTAN gelen bir dizedir.
    api = FakeApi()
    api.document = {**api.document, "filename": "../../../etc/kotu.csv"}
    sonuc = await make_service(api=api, export_dir=tmp_path).export(actor="Ayşe")
    assert "/" not in sonuc["name"]
    assert (tmp_path / sonuc["name"]).exists()


async def test_kesilmis_dosya_hata_degil_ama_soylenir(tmp_path: Any) -> None:
    api = FakeApi()
    api.document = {**api.document, "truncated": True, "total_rows": 20000}
    sonuc = await make_service(api=api, export_dir=tmp_path).export(actor="Ayşe")
    assert sonuc["ok"] is True
    assert sonuc["truncated"] is True


async def test_disa_aktarim_liste_ile_ayni_suzgeci_kullanir(tmp_path: Any) -> None:
    # CSV, ekranda görünen kümenin ta kendisi olmalı.
    api = FakeApi()
    servis = make_service(api=api, export_dir=tmp_path)
    await servis.orders(status="yeni,hazir", source="subscription")
    await servis.export(actor="Ayşe", status="yeni,hazir", source="subscription")
    liste = api.used("order_list")[0]
    disa = api.used("export_orders")[0]
    assert liste["status"] == disa["status"] == ["yeni", "hazir"]
    assert liste["source"] == disa["source"] == "subscription"


async def test_disa_aktarim_bozuk_suzgecte_gecide_gitmez(tmp_path: Any) -> None:
    api = FakeApi()
    sonuc = await make_service(api=api, export_dir=tmp_path).export(
        actor="Ayşe", date_from="16.08.2026")
    assert sonuc["ok"] is False
    assert api.names() == []


# ============================================================ ekran tercihi

async def test_tercih_yazilir_ve_geri_okunur() -> None:
    store = FakeStore()
    servis = make_service(store=store)
    sonuc = await servis.save_prefs({"page_size": 50, "auto_refresh": False},
                                    actor="Ayşe")
    assert sonuc["ok"] is True
    assert sonuc["prefs"]["page_size"] == 50
    assert sonuc["prefs"]["auto_refresh"] is False


async def test_taninmayan_tercih_reddedilir() -> None:
    # Yazım hatasını sessizce diske yazıp hiçbir yerde kullanmamak olurdu.
    store = FakeStore()
    sonuc = await make_service(store=store).save_prefs({"sayfa": 50}, actor="Ayşe")
    assert sonuc["ok"] is False
    assert store.prefs == {}


async def test_tercih_okunamazsa_modul_ayari_gecerli() -> None:
    store = FakeStore()
    store.broken = True
    tercih = await make_service(store=store, config={"page_size": 40}).prefs()
    assert tercih["page_size"] == 40


# ================================================================ yerel iz

async def test_yerel_iz_en_yeniden_eskiye_doner() -> None:
    store = FakeStore()
    servis = make_service(store=store)
    await servis.set_status(8421, status="hazir", reason=GEREKCE, actor="Ayşe",
                            dry_run=False)
    sonuc = await servis.audit(limit=10)
    assert sonuc["ok"] is True
    assert sonuc["items"][0]["result"] == "ok"
    assert sonuc["items"][0]["action"] == "order.status"
