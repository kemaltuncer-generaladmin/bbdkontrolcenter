"""Müşteriler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from store_customers_backend import analytics
from store_customers_backend.service import CustomerCard, CustomersService
from store_customers_fakes import FakeApi, FakeLog, FakeScan, FakeStore

# TARİHLER BUGÜNE GÖRE KURULUR. Sabit tarih yazmak testi takvime bağlar:
# "2026-08-01 siparişi" bugün taze, altı ay sonra uykudadır ve test kimse
# koda dokunmadan kırılır.
TODAY = date.fromisoformat(analytics.today_iso())


def _gun(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


MUSTERI = {
    "id": 7, "first_name": "Ayşe", "last_name": "Yılmaz", "email": "ayse@ornek.tr",
    "phone": "5321234567", "status": 1, "orders_count": 4, "total_spent": "1200.00",
    "last_order_date": _gun(12), "created_at": _gun(900),
    "subscribed_to_news_letter": 1, "customer_group": {"id": 2, "name": "Öğretmen"},
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[CustomersService, FakeApi, FakeStore]:
    api = api or FakeApi({7: dict(MUSTERI)})
    store = store or FakeStore()
    service = CustomersService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
        scan=FakeScan(),
    )
    return service, api, store


def _tarama(service: CustomersService) -> FakeScan:
    scan = service._scan_cache
    assert isinstance(scan, FakeScan)
    return scan


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("customers")
    result = await service.customers()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_kunye_parcasi_patlarsa_gerisi_yine_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("orders")
    result = await service.card(7)
    assert result["ok"] is True
    assert result["customer"]["email"] == "ayse@ornek.tr"
    assert any("siparişler" in item for item in result["warnings"])


async def test_iade_ucu_yayinda_degilse_sekme_bunu_soyler() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_return_requests")
    result = await service.returns(7)
    assert result["ok"] is True
    assert result["available"] is False


# ================================================= canlı liste vs tarama yolu

async def test_basit_suzgecler_sunucuya_gider_tarama_yapilmaz() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)],
                        "meta": {"total": 940, "currentPage": 2, "perPage": 50, "lastPage": 19}}
    result = await service.customers(q="ayse@ornek.tr", group_id=2, status="1", page=2)
    assert result["source"] == "customers"
    assert result["total"] == 940
    filters = api.args_of("customers")[0][0]
    assert filters["email"] == "ayse@ornek.tr"      # "@" içeren metin e-postaya gider
    assert filters["customer_group_id"] == 2
    assert api.used("customers")[0]["all_pages"] is False


async def test_telefonla_arama_telefon_alanina_gider() -> None:
    service, api, _ = _service()
    await service.customers(q="5321234567")
    assert api.args_of("customers")[0][0]["phone"] == "5321234567"


async def test_segment_suzgeci_secilince_nufus_taranir() -> None:
    # Bagisto müşteri ucu sipariş toplamına göre süzdürmüyor; segment süzgeci
    # ancak nüfusun tamamı elimizdeyken uygulanabilir.
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI),
                                  {**MUSTERI, "id": 8, "orders_count": 0, "total_spent": "0"}],
                        "meta": {}}
    result = await service.customers(segment="loyal")
    assert result["source"] == "segment"
    assert [row["id"] for row in result["items"]] == [7]
    assert api.used("customers")[0]["all_pages"] is True


async def test_tarama_bellekte_tutulur_her_suzgecte_yenilenmez() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    await service.customers(segment="loyal")
    await service.customers(segment="lost")
    assert len(api.used("customers")) == 1


async def test_tarama_yenilenmesi_istenirse_tekrar_cekilir() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    await service.customers(segment="loyal")
    _tarama(service).age("population", "order_stats")
    await service.customers(segment="loyal", refresh=True)
    assert len(api.used("customers")) == 2


async def test_az_once_biten_tarama_yenile_ile_yeniden_baslatilmaz() -> None:
    # "Yenile"ye arka arkaya basmak, az önce biten ~36 sayfalık nüfus
    # taramasını yeniden başlatıp geçidin ortak kovasını boşa harcıyordu.
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    await service.overview()
    await service.overview(refresh=True)
    assert len(api.used("customers")) == 1
    assert len(api.used("orders")) == 1


async def test_yazma_sonrasi_yenile_hemen_calisir() -> None:
    # Bayatlatılmış (yazma sonrası) tarama bir dakikayı beklemez: kullanıcının
    # kendi değişikliği yeniden okumayı hak eder.
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    await service.overview()
    await service.set_status([7], active=False, reason="Müşteri hesabını kapattı",
                             actor="Ali")
    await service.overview(refresh=True)
    assert len(api.used("customers")) == 2
    assert len(api.used("orders")) == 1        # sipariş taraması bayatlamadı


async def test_tarama_patlarsa_bayat_kopya_hatasiyla_birlikte_doner() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    await service.customers(segment="loyal")
    _tarama(service).age("population", "order_stats")
    api.fail.add("customers")
    result = await service.customers(segment="loyal", refresh=True)
    assert result["items"]                    # eldeki kopya gösterilir
    assert result["connected"] is False       # ama bayat olduğu söylenir
    assert "patladı" in result["error"]


async def test_harcama_araligi_da_tarama_yolunu_acar() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI)], "meta": {}}
    result = await service.customers(spend_min=100_000)
    assert result["source"] == "segment"
    assert api.used("customers")[0]["all_pages"] is True


async def test_mini_kpi_tek_taramadan_hesaplanir() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(MUSTERI),
                                  {**MUSTERI, "id": 8, "orders_count": 1}], "meta": {}}
    result = await service.overview()
    assert result["kpi"]["total"] == 2
    assert result["kpi"]["repeatRate"] == 50.0      # 4 siparişli tekrar eden, 1 siparişli değil
    assert result["segments"] == {**dict.fromkeys(result["segments"], 0),
                                  "loyal": 1, "new": 1}


# ================================================ müşteri künyesi ve süzgeç

async def test_baskasinin_siparisi_kunyede_gosterilmez() -> None:
    # En kötü hata sınıfı: başka müşterinin siparişini bu müşteriye ait
    # göstermek. Mağazanın `customer_id` süzgecine HİÇ güvenilmez; liste arka
    # plan taramasının dizininden, satırdaki kimliğe göre kurulur.
    service, api, _ = _service()
    api.orders_payload = {"items": [{"id": 1, "customer_id": 99, "grand_total": "10.00"}],
                          "meta": {}}
    result = await service.card(7)
    assert result["orders"] == []
    assert result["ordersHonored"] is False
    assert result["ordersLocalFiltered"] is True
    # Mağazaya `customer_id` süzgeci GÖNDERİLMEDİ: tarama süzgeçsizdir.
    assert api.args_of("orders") == [({},)]


async def test_toplamlar_kayitta_yoksa_siparislerden_hesaplanir() -> None:
    api = FakeApi({7: {k: v for k, v in MUSTERI.items()
                       if k not in ("orders_count", "total_spent", "last_order_date")}})
    api.orders_payload = {"items": [
        {"id": 1, "customer_id": 7, "grand_total": "100.00", "created_at": _gun(2)},
        {"id": 2, "customer_id": 7, "grand_total": "50.00", "created_at": _gun(40)},
    ], "meta": {}}
    service, _, _ = _service(api)
    result = await service.card(7)
    assert result["customer"]["orders"] == 2
    assert result["customer"]["spend"] == 15000
    assert result["customer"]["computed"] is True


async def test_alti_aylik_seri_kunyede_uretilir() -> None:
    service, api, _ = _service()
    api.orders_payload = {"items": [{"id": 1, "customer_id": 7, "grand_total": "100.00",
                                     "created_at": TODAY.isoformat()}], "meta": {}}
    result = await service.card(7)
    assert len(result["spark"]) == 6
    assert result["spark"][-1]["total"] == 10000        # son kova bu ay


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.save(7, patch={"phone": "5551112233"}, reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("update_customer") == []


async def test_kaydetme_oku_degistir_yaz_yapar() -> None:
    service, api, _ = _service()
    result = await service.save(7, patch={"phone": "5551112233"},
                                reason="Müşteri telefonunu güncelledi", actor="Ali",
                                dry_run=False)
    assert result["ok"] is True
    body = api.used("update_customer")[0]["payload"]
    assert body["phone"] == "5551112233"
    assert body["email"] == "ayse@ornek.tr"      # dokunulmayan alan korunur
    assert body["channel"] == "default"


async def test_durum_kaydet_ucundan_degistirilemez() -> None:
    # Pasifleştirme ayrı izin ister; kaydet düğmesinin arkasına saklanmaz.
    service, api, _ = _service()
    result = await service.save(7, patch={"status": 0},
                                reason="Hesap kapatılacak diye", actor="Ali")
    assert result["ok"] is False
    assert result["error"] == "Değişen alan yok."
    assert api.used("update_customer") == []


async def test_taninmayan_alan_magazaya_gonderilmez() -> None:
    service, api, _ = _service()
    await service.save(7, patch={"phone": "5551112233", "uydurma": "x"},
                       reason="Telefon düzeltildi çünkü", actor="Ali", dry_run=False)
    body = api.used("update_customer")[0]["payload"]
    assert "uydurma" not in body


async def test_bulten_degisikligi_izin_gecmisine_yazilir() -> None:
    service, _, store = _service()
    await service.save(7, patch={"newsletter": False},
                       reason="Müşteri bülten aboneliğini iptal etti", actor="Ayşe Yılmaz",
                       dry_run=False)
    kayit = store.consent[-1]
    assert kayit["kind"] == "newsletter"
    assert kayit["before_value"] == "True"
    assert kayit["after_value"] == "False"
    assert kayit["reason"] == "Müşteri bülten aboneliğini iptal etti"


async def test_kuru_provada_izin_gecmisi_yazilmaz() -> None:
    service, _, store = _service()
    await service.save(7, patch={"newsletter": False},
                       reason="Müşteri bülten aboneliğini iptal etti", actor="Ali",
                       dry_run=True)
    assert store.consent == []


# ============================================================ pasifleştirme

async def test_pasiflestirme_toplu_ucu_kullanir_silme_ucu_yoktur() -> None:
    service, api, _ = _service()
    result = await service.set_status([7, 8], active=False,
                                      reason="Sahte kayıtlar kapatıldı", actor="Ali",
                                      dry_run=False)
    assert result["ok"] is True
    assert api.used("update_customer_status")[0]["active"] is False
    assert not hasattr(service, "delete_customer")


async def test_bos_secim_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.set_status([], active=False, reason="Toplu kapatma yapılacak",
                                      actor="Ali")
    assert result["ok"] is False
    assert api.used("update_customer_status") == []


# =================================================================== KVKK

async def test_acik_kvkk_talebi_yoksa_anonimlestirme_yapilmaz() -> None:
    # Elle "ad/e-postayı üzerine yaz" yolu BİLEREK yok: sipariş ve fatura
    # kayıtlarındaki kişisel veri yerinde kalır ve ekran işi bitirdiğini
    # sandırırdı — KVKK'da en tehlikeli hata.
    service, api, _ = _service()
    api.gdpr_payload = {"items": [], "meta": {}}
    result = await service.anonymize(7, reason="Müşteri silinme talebi gönderdi", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is False
    assert result["available"] is False
    assert api.used("process_gdpr_request") == []


async def test_acik_talep_varsa_anonimlestirme_talep_uzerinden_yurur() -> None:
    service, api, store = _service()
    api.gdpr_payload = {"items": [{"id": 12, "customer_id": 7, "type": "delete",
                                   "status": "pending"}], "meta": {}}
    result = await service.anonymize(7, reason="Müşteri silinme talebi gönderdi", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is True
    assert api.args_of("process_gdpr_request")[0][0] == 12
    assert "GERİ ALINAMAZ" in result["notice"]
    assert any(row["action"] == "anonymize" and row["result"] == "ok" for row in store.audit)


async def test_anonimlestirme_kisa_gerekceyle_yapilmaz() -> None:
    service, api, _ = _service()
    result = await service.anonymize(7, reason="sil", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("gdpr_requests") == []


async def test_veri_paketi_magazada_uretilir_burada_toplanmaz() -> None:
    service, api, _ = _service()
    result = await service.gdpr_export(7, reason="Müşteri veri talebinde bulundu",
                                       actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["url"] == "https://ornek/paket.zip"
    assert api.used("gdpr_download_data")[0]["reason"] == "Müşteri veri talebinde bulundu"


# ================================================================== yorum

async def test_tek_puan_sunucuya_gider_coklu_puan_sayfada_suzulur() -> None:
    service, api, _ = _service()
    api.reviews_payload = {"items": [
        {"id": 1, "rating": 5, "status": "approved"},
        {"id": 2, "rating": 2, "status": "pending"},
        {"id": 3, "rating": 4, "status": "pending"},
    ], "meta": {}}
    tek = await service.reviews(ratings="5")
    assert api.args_of("reviews")[0][0]["rating"] == 5
    assert tek["clientFiltered"] is False

    api.calls.clear()
    coklu = await service.reviews(ratings="4,5")
    assert "rating" not in api.args_of("reviews")[0][0]
    assert [row["id"] for row in coklu["items"]] == [1, 3]
    assert coklu["clientFiltered"] is True


async def test_spam_magazada_reddetmeye_donusur_ve_yerelde_etiketlenir() -> None:
    service, api, store = _service()
    result = await service.moderate([4], action="spam", reason="Reklam içeriyor bu yorum",
                                    actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("update_review_status")[0]["status"] == "disapproved"
    assert store.flags[4]["spam"] == 1
    assert "spam durumu yok" in result["notice"]


async def test_onaylama_spam_etiketini_kaldirir() -> None:
    service, _, store = _service()
    await service.moderate([4], action="spam", reason="Reklam içeriyor bu yorum",
                           actor="Ali", dry_run=False)
    await service.moderate([4], action="approve", reason="Yanlış işaretlenmiş görüldü",
                           actor="Ali", dry_run=False)
    assert store.flags[4]["spam"] == 0


async def test_bilinmeyen_yorum_islemi_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.moderate([4], action="yok_boyle", reason="Deneme yapılıyor işte",
                                    actor="Ali")
    assert result["ok"] is False
    assert api.used("update_review_status") == []


async def test_kuru_provada_yerel_etiket_yazilmaz() -> None:
    service, _, store = _service()
    await service.moderate([4], action="spam", reason="Reklam içeriyor bu yorum",
                           actor="Ali", dry_run=True)
    assert store.flags == {}


async def test_magaza_yaniti_ayardaki_alan_adiyla_gonderilir() -> None:
    service, api, store = _service(review_reply_field="admin_reply")
    result = await service.reply(4, body="Geri bildiriminiz için teşekkürler.",
                                 reason="Müşteriye yanıt veriliyor", actor="Ali",
                                 dry_run=False)
    assert result["ok"] is True
    assert api.used("update_review")[0]["payload"] == {
        "admin_reply": "Geri bildiriminiz için teşekkürler."}
    assert store.flags[4]["reply"].startswith("Geri bildiriminiz")


async def test_yanit_reddedilirse_hangi_ayardan_duzeltilecegi_yazilir() -> None:
    service, api, _ = _service()
    api.fail.add("update_review")
    result = await service.reply(4, body="Teşekkürler efendim.",
                                 reason="Müşteriye yanıt veriliyor", actor="Ali",
                                 dry_run=False)
    assert result["ok"] is False
    assert "review_reply_field" in result["error"]


async def test_musterinin_yorumlari_suzgec_uygulanmadiysa_gosterilmez() -> None:
    service, api, _ = _service()
    api.reviews_payload = {"items": [{"id": 1, "rating": 5, "status": "approved",
                                      "customer": {"id": 99}}], "meta": {}}
    result = await service.reviews(customer_id=7)
    assert result["items"] == []
    assert "süzgecini uygulamadı" in result["error"]


# ================================================================= ayarlar

async def test_bulunmayan_ayar_anahtarina_yazilmaz() -> None:
    # Bulunmayan anahtara yazmak `core_config` içinde etkisiz bir satır açar ve
    # kullanıcı ayarı değiştirdiğini sanır — sessiz başarısızlık.
    service, api, _ = _service(gdpr_config_slug="customer.settings")
    api.config_payload = {"values": {}}
    result = await service.save_settings(values={"emailVerification": True},
                                         reason="Doğrulama açılıyor", actor="Ali",
                                         dry_run=False)
    assert result["ok"] is True
    assert "E-posta doğrulama" in result["skipped"]
    assert api.used("update_configuration") == []


async def test_bulunan_ayar_anahtari_yazilir() -> None:
    service, api, _ = _service(gdpr_config_slug="customer.settings")
    api.config_payload = {"values": {"customer.settings.registration.verification": 0}}
    result = await service.save_settings(values={"emailVerification": True},
                                         reason="Doğrulama açılıyor", actor="Ali",
                                         dry_run=False)
    assert result["skipped"] == []
    assert api.used("update_configuration")[0]["values"] == {
        "customer.settings.registration.verification": 1}


async def test_ayar_kaydi_kisa_gerekceyle_yapilmaz() -> None:
    service, api, _ = _service()
    result = await service.save_settings(values={"emailVerification": True}, reason="ok",
                                         actor="Ali")
    assert result["ok"] is False
    assert api.used("update_configuration") == []


# ============================================================== denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.save(7, patch={"phone": "5551112233"},
                       reason="Müşteri telefonunu güncelledi", actor="Ayşe Yılmaz",
                       dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "update_customer"]
    assert kayitlar[-1]["reason"] == "Müşteri telefonunu güncelledi"
    assert kayitlar[-1]["actor"] == "Ayşe Yılmaz"
    assert kayitlar[-1]["result"] == "ok"


async def test_yazma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store = _service()
    api.fail.add("update_customer")
    await service.save(7, patch={"phone": "5551112233"},
                       reason="Müşteri telefonunu güncelledi", actor="Ali", dry_run=False)
    assert any(row["result"] == "hata" for row in store.audit)


async def test_denetim_izi_musteriye_gore_suzulur() -> None:
    service, _, _ = _service()
    await service.save(7, patch={"phone": "5551112233"},
                       reason="Müşteri telefonunu güncelledi", actor="Ali", dry_run=False)
    result = await service.audit(customer_id=7)
    assert result["items"]
    assert all(row["customerId"] == 7 for row in result["items"])


# ============================================================= olay yayını

async def test_anonimlestirme_olayi_yayinlanir_ama_hata_isi_kesmez() -> None:
    gonderilen: list[tuple[str, dict[str, Any]]] = []

    async def publish(name: str, payload: dict[str, Any]) -> None:
        gonderilen.append((name, payload))
        raise RuntimeError("dinleyici patladı")

    api = FakeApi({7: dict(MUSTERI)})
    api.gdpr_payload = {"items": [{"id": 12, "customer_id": 7, "status": "pending"}],
                        "meta": {}}
    service = CustomersService(api=api, store=FakeStore(), log=FakeLog(), config={},
                               publish=publish, fallback_dir=Path("/tmp/km-test-raporlar"),
                               scan=FakeScan())
    result = await service.anonymize(7, reason="Müşteri silinme talebi gönderdi", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is True                      # olay hatası işi kesmedi
    assert gonderilen[0][0] == "store.customer.anonymized"


# =========================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_bastirilmaz() -> None:
    # Serbest yol kabul etmek `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    service, _, _ = _service()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False


async def test_yazici_yoksa_ekran_bunu_soyler() -> None:
    service, _, _ = _service()
    status = await service.printer_status()
    assert status["ready"] is False
    assert "Yazıcı" in status["error"]


# ==================================================== store.customer.card

async def test_yetenek_ozeti_tek_satirlik_kunye_verir() -> None:
    service, _, _ = _service()
    card = CustomerCard(service)
    summary = await card.summary(7)
    assert summary["ok"] is True
    assert summary["name"] == "Ayşe Yılmaz"
    assert summary["segmentLabel"] == "Sadık"


async def test_yetenek_ozeti_magaza_dusunce_patlamaz() -> None:
    service, api, _ = _service()
    api.fail.add("customer")
    summary = await CustomerCard(service).summary(7)
    assert summary["ok"] is False
    assert summary["customerId"] == 7


# ============================ CANLI BİÇİM VE CANLI DAVRANIŞ (bbdstore.com.tr)
#
# Bu bölümdeki sözlükler ve varsayımlar canlı mağazadan SALT OKUMA ile
# doğrulanmıştır. Sahte snake_case veriyle yazılan testler geçerken ekran
# canlıda boş görünüyordu; buradaki testler o hatanın geri gelmesini engeller.

CANLI_MUSTERI = {
    "id": 12, "firstName": "veysel kemal", "lastName": "TUNCER",
    "name": "veysel kemal TUNCER", "email": "ornek@ornek.tr", "phone": "905337695687",
    "gender": None, "dateOfBirth": None, "channelId": 1, "status": 1,
    "subscribedToNewsLetter": True, "isVerified": 1, "createdAt": _gun(40),
    "group": {"id": 2, "code": "general", "name": "Genel"},
}

#: Liste ucu sipariş/harcama VERMİYOR — canlıda doğrulandı.
CANLI_MUSTERI_LISTE = {key: value for key, value in CANLI_MUSTERI.items()
                       if key not in ("totalOrders", "totalAmountSpent")}


def _siparis(order_id: int, customer_id: int | None, gun: str,
             tutar: str = "10.00") -> dict[str, Any]:
    return {"id": order_id, "incrementId": str(order_id), "status": "processing",
            "statusLabel": "Processing", "customerId": customer_id, "grandTotal": tutar,
            "createdAt": f"{gun} 09:00:00", "location": "MERKEZ, Kırşehir, TR",
            "totalQtyOrdered": 1}


async def test_magaza_musteri_suzgecini_uygulamazsa_liste_yerelde_suzulur() -> None:
    # CANLIDA DOĞRULANDI: /api/admin/orders?customer_id=12 süzgeci UYGULAMIYOR,
    # 17 siparişin tamamını döndürüyor. Kimlik doğrulanmadan gösterilirse
    # başkasının siparişi bu müşteriye ait görünür — en pahalı hata sınıfı.
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.orders_payload = {"items": [_siparis(1, 12, _gun(5)), _siparis(2, 99, _gun(4)),
                                    _siparis(3, 12, _gun(3))], "meta": {}}
    result = await service.card(12)
    assert [row["id"] for row in result["orders"]] == [3, 1]     # en yeni başta
    assert result["ordersHonored"] is False
    assert result["ordersLocalFiltered"] is True
    assert result["customer"]["orders"] == 2          # 3 değil: yabancı sipariş sayılmadı


async def test_kimligi_dogrulanamayan_siparisler_musteriye_yazilmaz() -> None:
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.orders_payload = {"items": [{"id": 1, "grandTotal": "10.00",
                                     "createdAt": f"{_gun(2)} 09:00:00"}], "meta": {}}
    result = await service.card(12)
    assert result["orders"] == []
    assert result["ordersHonored"] is None
    assert result["ordersUnverifiable"] is True       # ekran bunu ayrı bayraktan okur
    assert result["customer"]["orders"] is None       # sayı UYDURULMADI
    assert any("doğrulanamadı" in item for item in result["warnings"])


async def test_liste_sayilari_siparislerden_toplulastirilir() -> None:
    # Mağazanın müşteri LİSTE ucu sipariş sayısı, harcama ve son sipariş
    # tarihi vermiyor; üçü de siparişlerden sayılmazsa tablo "—" ile dolar.
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {"total": 12}}
    api.orders_payload = {"items": [_siparis(1, 12, _gun(9), "100.00"),
                                    _siparis(2, 12, _gun(3), "50.00")], "meta": {}}
    result = await service.customers()
    row = result["items"][0]
    assert row["orders"] == 2
    assert row["spend"] == 15_000
    assert row["lastOrderAt"] == _gun(3)
    assert row["city"] == "MERKEZ"                    # sipariş `location` alanından
    assert row["segment"] != "unknown"
    assert row["computed"] is True
    assert result["statsError"] == ""


async def test_siparis_taramasi_patlarsa_sayilar_uydurulmaz() -> None:
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {}}
    api.fail.add("orders")
    result = await service.customers()
    row = result["items"][0]
    assert row["orders"] is None and row["segment"] == "unknown"
    assert result["ok"] is True and result["connected"] is True     # ekran ayakta (K7)
    assert "patladı" in result["statsError"]


async def test_dogrulanmamis_suzgeci_sunucuya_gonderilmez() -> None:
    # Mağazanın müşteri ucunda `status` yalnız 0/1'dir; "unverified" gönderilse
    # Laravel onu sessizce yok sayar ve ekran HERKESİ "doğrulanmamış" diye
    # gösterirdi. Bu süzgeç taramada uygulanır.
    service, api, _ = _service()
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE),
                                  {**CANLI_MUSTERI_LISTE, "id": 26, "isVerified": 0}],
                        "meta": {}}
    result = await service.customers(status="unverified")
    assert result["source"] == "segment"
    assert [row["id"] for row in result["items"]] == [26]
    assert api.used("customers")[0]["all_pages"] is True


async def test_kunye_guncellemesi_gomulu_gruptan_okunur() -> None:
    # Canlı kayıtta `customer_group_id` YOK, grup `group: {id,…}` içinde gömülü.
    # Okunamazsa güncelleme müşterinin grubunu sıfırlardı.
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    result = await service.save(12, patch={"phone": "5301112233"},
                                reason="Müşteri telefonunu güncelledi", actor="Ali",
                                dry_run=False)
    assert result["ok"] is True
    body = api.used("update_customer")[0]["payload"]
    assert body["customer_group_id"] == 2
    assert body["first_name"] == "veysel kemal"        # camelCase kayıttan okundu
    assert body["email"] == "ornek@ornek.tr"           # dokunulmayan alan korundu
    assert body["phone"] == "5301112233"


async def test_ayar_bolumu_okunamazsa_yanlis_teshis_konmaz() -> None:
    # Bölüm BOŞ ZARF ile dönerse bunu "anahtar bulunamadı" saymak kullanıcıyı
    # anahtar adlarını düzeltmeye uğraştırır; sorun orada değildir.
    #
    # Bu dal bir dönem geçidin tekil-kayıt yolunun tek elemanlı listeyi
    # açamamasından tetikleniyordu; o kusur store_api'de giderildi
    # (2026-08-16'da canlıda ayarlar geliyor). DAL YİNE DE SINANIR: bölüm
    # başka bir sebeple okunamazsa ekran ayar YAZMAMALI.
    service, api, _ = _service()
    api.config_payload = {}
    okunan = await service.settings()
    assert okunan["storeAvailable"] is False
    assert "BOŞ ZARF" in okunan["error"]
    result = await service.save_settings(values={"emailVerification": True},
                                         reason="Doğrulama açılıyor", actor="Ali",
                                         dry_run=False)
    assert result["ok"] is False
    assert api.used("update_configuration") == []


async def test_varsayilan_grup_kod_olarak_yazilir() -> None:
    # Mağaza varsayılan grubu KODLA tutuyor (`general`); kimlik yazmak ayarı
    # sessizce bozar.
    service, api, _ = _service(gdpr_config_slug="customer.settings")
    anahtar = "customer.settings.create_new_account_options.default_group"
    api.config_payload = {"values": {anahtar: "general"}}
    result = await service.save_settings(values={"defaultGroup": "wholesale"},
                                         reason="Toptan grubuna alınıyor", actor="Ali",
                                         dry_run=False)
    assert result["skipped"] == []
    assert api.used("update_configuration")[0]["values"] == {anahtar: "wholesale"}


async def test_musteri_gruplari_kodlariyla_birlikte_doner() -> None:
    service, api, _ = _service(gdpr_config_slug="customer.settings")
    api.config_payload = {"values": {"customer.settings.email.verification": "1"}}
    okunan = await service.settings()
    assert okunan["groups"][0]["code"] == "general"


async def test_yorum_durum_suzgeci_uygulanmazsa_sayfada_suzulur() -> None:
    # Durum süzgeci tutmazsa "Bekleyen" kuyruğunda onaylı yorumlar görünür ve
    # moderatör kuyruğun bittiğini sanar.
    service, api, _ = _service()
    api.reviews_payload = {"items": [
        {"id": 1, "rating": 5, "status": "pending", "createdAt": "2026-08-01 09:00:00"},
        {"id": 2, "rating": 4, "status": "approved", "createdAt": "2026-08-02 09:00:00"},
    ], "meta": {"total": 2}}
    result = await service.reviews(status="pending")
    assert [row["id"] for row in result["items"]] == [1]
    assert result["clientFiltered"] is True


async def test_es_zamanli_istekler_nufusu_iki_kez_taramaz() -> None:
    # Ekran açılışında liste ve KPI uçları aynı anda istenir. Kilit olmasaydı
    # ikisi de nüfusu ve siparişleri baştan tarar; mağazanın 60 istek/dk
    # sınırı boşuna iki katı yenirdi.
    service, api, _ = _service()
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {}}
    await asyncio.gather(service.overview(), service.customers(segment="loyal"))
    assert len(api.used("customers")) == 1
    assert len(api.used("orders")) == 1


async def test_magazanin_toplami_siparis_toplamindan_farkliysa_soylenir() -> None:
    # CANLIDA: müşteri 12 için mağaza 35.499 TL diyor, siparişlerin toplamı
    # 36.326 TL (iptal/bekleyen siparişler). Liste toplulaştırmayı, künye
    # mağazanın sayısını gösteriyor; fark söylenmezse aynı müşteri iki ekranda
    # iki farklı rakam gösterir.
    service, api, _ = _service(FakeApi({12: {**CANLI_MUSTERI, "totalOrders": 1,
                                             "totalAmountSpent": "10.00"}}))
    api.orders_payload = {"items": [_siparis(1, 12, _gun(5), "10.00"),
                                    _siparis(2, 12, _gun(4), "5.00")], "meta": {}}
    result = await service.card(12)
    assert result["customer"]["spend"] == 1000          # mağazanınki kazandı
    assert result["customer"]["computed"] is False
    assert any("sipariş listesinin toplamı" in item for item in result["warnings"])


# ============================================ tarama isteğin içinde koşmaz

class AskidaTarama:
    """Tarama BAŞLADI ama bitmedi. Gerçek hayatta dakikalar süren durum.

    Kabuk isteği 60 saniyede kesiyor; liste bu taramayı beklerse müşteri ekranı
    HİÇ açılmaz — yaşanan arıza buydu. Bu sahte katman yükleyiciyi hiç
    çağırmaz ve her zaman "sürüyor" der.
    """

    def __init__(self) -> None:
        self.istekler: list[str] = []

    def scoped(self, namespace: str) -> AskidaTarama:
        return self

    def invalidate(self, key: str) -> None:
        return None

    def peek(self, key: str) -> dict[str, Any]:
        return {"state": "running", "value": None, "stale": True, "running": True,
                "at": "", "ageSeconds": 0, "error": ""}

    async def read(self, key: str, loader: Any, *, ttl: int | None = None,
                   refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        self.istekler.append(key)
        return self.peek(key)


def _askida_servis() -> tuple[CustomersService, FakeApi, AskidaTarama]:
    api = FakeApi({12: dict(CANLI_MUSTERI)})
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {"total": 1}}
    tarama = AskidaTarama()
    service = CustomersService(
        api=api, store=FakeStore(), log=FakeLog(), config={},
        fallback_dir=Path("/tmp/km-test-raporlar"), scan=tarama)
    return service, api, tarama


async def test_liste_siparis_taramasini_beklemez() -> None:
    service, _api, tarama = _askida_servis()
    result = await asyncio.wait_for(service.customers(), timeout=2.0)
    assert result["ok"] is True and result["connected"] is True
    assert len(result["items"]) == 1              # liste GELDİ
    assert "order_stats" in tarama.istekler       # tarama istendi ama beklenmedi
    assert result["scan"]["state"] == "running"   # ekran nedenini söyleyebilsin


async def test_tarama_bitmeden_sifir_uydurulmaz() -> None:
    # `apply_order_stats` BOŞ haritayla çağrılsaydı herkese "0 sipariş" yazardı
    # ve siparişi olan müşteri "hiç almamış" segmentine düşerdi.
    service, _, _ = _askida_servis()
    row = (await service.customers())["items"][0]
    assert row["orders"] is None
    assert row["spend"] is None
    assert row["segment"] == "unknown"


async def test_segment_yolu_tarama_surerken_duz_listeyi_gosterir() -> None:
    # CANLI ÖNİZLEMEDE GÖRÜLDÜ: tarama sürerken ekran "Mağazaya ulaşılamadı"
    # diyordu (`connected: False`); bağlantıda sorun yoktu. Artık mağazanın tek
    # sayfası hemen gelir, segment süzgeci tarama bitince uygulanır.
    service, api, _ = _askida_servis()
    result = await asyncio.wait_for(service.customers(segment="loyal", q="veysel"),
                                    timeout=2.0)
    assert result["connected"] is True and result["error"] == ""
    assert result["pending"] is True
    assert [row["id"] for row in result["items"]] == [12]
    assert result["scan"]["running"] is True
    # Mağazaya yalnız uyguladığı süzgeç gider; tek sayfa, tarama değil.
    assert api.used("customers")[-1]["all_pages"] is False
    assert api.args_of("customers")[-1][0] == {"name": "veysel"}


async def test_tarama_patlarsa_duz_liste_ve_tek_satir_neden_doner() -> None:
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {"total": 1}}
    original = api.customers

    async def tam_tarama_patlar(filters: Any = None, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("all_pages"):
            raise RuntimeError("tarama patladı")
        return await original(filters, **kwargs)

    api.customers = tam_tarama_patlar  # type: ignore[method-assign]
    result = await service.customers(segment="loyal")
    assert result["connected"] is True and result["pending"] is False
    assert result["items"]                             # liste yine geldi
    assert result["segmentError"]                      # ama neden yazıyor


async def test_magaza_da_yanit_vermezse_gercek_baglanti_hatasi_doner() -> None:
    service, api, _ = _askida_servis()
    api.fail.add("customers")
    result = await service.customers(segment="loyal")
    assert result["connected"] is False
    assert "patladı" in result["error"]


async def test_yetenek_yoksa_ekran_yine_acilir() -> None:
    # `store.scan` çözülemezse (yalıtılmış kurulum) tarama hiç yapılmaz ama
    # liste yine gelir; sayılar boş kalır (K7).
    api = FakeApi({12: dict(CANLI_MUSTERI)})
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {"total": 1}}
    service = CustomersService(api=api, store=FakeStore(), log=FakeLog(), config={},
                               fallback_dir=Path("/tmp/km-test-raporlar"), scan=None)
    result = await service.customers()
    assert result["ok"] is True and len(result["items"]) == 1
    assert result["items"][0]["segment"] == "unknown"
    assert api.used("orders") == []               # tam tarama HİÇ denenmedi


# ================================ künye ve yetenek mağazada sipariş TARAMAZ
#
# BULUNAN ARIZA (2026-10-10): tek bir müşteriyi açmak
# `orders({"customer_id": …}, all_pages=True)` çağırıyordu; mağaza bu süzgeci
# uygulamadığı için bu TÜM siparişleri sayfa sayfa taramaktı — istek içinde,
# geçidin ortak kovasını (dk 55) tüketerek. `store.customer.card` da aynı
# yoldan geçiyordu. Künye artık arka plan sipariş taramasının dizininden okur.

async def test_kunye_tarama_bitmeden_beklemez_ve_siparis_istemez() -> None:
    service, api, _ = _askida_servis()
    result = await asyncio.wait_for(service.card(12), timeout=2.0)
    assert result["ok"] is True
    assert result["ordersPending"] is True            # "hesaplanıyor"
    assert result["orders"] == []
    assert result["spark"] == []                      # altı ay sıfır ÇİZİLMEZ
    assert result["customer"]["orders"] is None      # sayı uydurulmadı
    assert [name for name, _, _ in api.calls] == ["customer"]   # tek istek


async def test_kunyeler_ve_liste_tek_siparis_taramasini_paylasir() -> None:
    service, api, _ = _service(FakeApi({7: dict(MUSTERI), 12: dict(CANLI_MUSTERI)}))
    api.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {"total": 1}}
    api.orders_payload = {"items": [_siparis(1, 12, _gun(5)), _siparis(2, 7, _gun(4))],
                          "meta": {}}
    await service.customers()
    first = await service.card(12)
    second = await service.card(7)
    assert [row["id"] for row in first["orders"]] == [1]
    assert [row["id"] for row in second["orders"]] == [2]
    assert len(api.used("orders")) == 1               # üç ekran, TEK tarama
    assert api.used("orders")[0]["all_pages"] is True


async def test_yetenek_ozeti_taramayi_beklemez() -> None:
    # Başka ekranların sorduğu rozet: mağazaya tek istek (müşteri detayı).
    service, api, _ = _askida_servis()
    summary = await asyncio.wait_for(CustomerCard(service).summary(12), timeout=2.0)
    assert summary["ok"] is True
    assert summary["ordersPending"] is True
    assert summary["orders"] is None                  # bilinmiyor, sıfır değil
    assert api.used("orders") == []


async def test_tarama_tavana_dayanirsa_kunye_sayi_hesaplamaz() -> None:
    service, api, _ = _service(FakeApi({12: dict(CANLI_MUSTERI)}))
    api.orders_payload = {"items": [_siparis(1, 12, _gun(5))], "meta": {},
                          "truncated": True}
    result = await service.card(12)
    assert [row["id"] for row in result["orders"]] == [1]   # liste gösterilir
    assert result["ordersPartial"] is True
    assert result["customer"]["orders"] is None             # ama sayı ondan çıkmaz
    assert result["spark"] == []
    assert any("tavana dayandı" in item for item in result["warnings"])


async def test_siparis_ucu_dogrudan_cagrilsa_da_tarama_yapmaz() -> None:
    service, api, _ = _askida_servis()
    result = await service.orders(12)
    assert result["pending"] is True and result["items"] == []
    assert api.used("orders") == []


# ====================================== taramalar sırayla koşar, yoklama ucuz

class _SayanApi(FakeApi):
    """Aynı anda kaç tarama koştuğunu sayar."""

    def __init__(self) -> None:
        super().__init__({12: dict(CANLI_MUSTERI)})
        self.list_payload = {"items": [dict(CANLI_MUSTERI_LISTE)], "meta": {}}
        self.active = 0
        self.peak = 0

    async def _scan(self) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1

    async def orders(self, filters: Any = None, **kwargs: Any) -> dict[str, Any]:
        await self._scan()
        return await super().orders(filters, **kwargs)

    async def customers(self, filters: Any = None, **kwargs: Any) -> dict[str, Any]:
        await self._scan()
        return await super().customers(filters, **kwargs)


class _ArkaPlanTarama:
    """Yükleyiciyi GERÇEKTEN arka plan görevi olarak başlatır, beklemez."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    def scoped(self, namespace: str) -> _ArkaPlanTarama:
        return self

    def invalidate(self, key: str) -> None:
        return None

    def peek(self, key: str) -> dict[str, Any]:
        task = self.tasks.get(key)
        running = task is not None and not task.done()
        return {"state": "running" if running else "empty", "value": None, "stale": True,
                "running": running, "at": "", "ageSeconds": 0, "error": ""}

    async def read(self, key: str, loader: Any, *, ttl: int | None = None,
                   refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        if key not in self.tasks:
            self.tasks[key] = asyncio.ensure_future(loader())
        return self.peek(key)


async def test_nufus_ve_siparis_taramasi_ayni_anda_kosmaz() -> None:
    # İki tarama aynı anda koştuğunda geçidin kovasını iki kat hızla
    # tüketiyor, o sırada açılan her ekran sıraya giriyordu.
    api = _SayanApi()
    tarama = _ArkaPlanTarama()
    service = CustomersService(api=api, store=FakeStore(), log=FakeLog(), config={},
                               fallback_dir=Path("/tmp/km-test-raporlar"), scan=tarama)
    await service.overview()                          # iki taramayı da başlatır
    assert set(tarama.tasks) == {"order_stats", "population"}
    await asyncio.gather(*tarama.tasks.values())
    assert api.peak == 1                              # hiçbir an ikisi birden koşmadı
    # Sipariş taraması ÖNCE istendi: kısa olan önce biter, sütunlar erken dolar.
    assert [name for name, _, _ in api.calls] == ["orders", "customers"]


async def test_tarama_durumu_magazaya_gitmez() -> None:
    service, api, _ = _askida_servis()
    status = service.scan_status()
    assert status["scan"]["running"] is True
    assert status["scan"]["runningKeys"] == ["order_stats", "population"]
    assert api.calls == []                            # yoklama ücretsiz


async def test_tarama_omru_yirmi_dakika() -> None:
    # 300 sn'de ekran açık durdukça her beş dakikada ~48 istek gidiyordu.
    service, _, _ = _service()
    assert service._scan_ttl == 1200
    default = (Path(__file__).resolve().parents[1] / "config" / "default.yaml").read_text(
        encoding="utf-8")
    assert "segment_scan_ttl: 1200" in default


async def test_nufus_taramasi_hizli_uctan_gelir_siparis_toplamlariyla() -> None:
    """Hızlı uç satır başına sipariş sayısı/harcama verir; segment, sipariş
    taraması bitmeden doğru çıkar. Vendor ucu hiç çağrılmaz."""
    service, api, _ = _service()

    async def hizli(filters: Any = None, **kw: Any) -> dict[str, Any]:
        assert kw.get("all_pages") is True
        return {"items": [{"id": 7, "first_name": "Ayşe", "last_name": "Kaya",
                           "email": "a@ornek.test", "orders_count": 4,
                           "total_spent": "1250.00", "last_order_date": "2026-10-01 10:00:00",
                           "city": "Konya", "created_at": "2026-01-01 10:00:00"}],
                "meta": {"total": 1}, "truncated": False}

    api.bbd_customers = hizli  # type: ignore[attr-defined]
    sonuc = await service._load_population()

    satir = sonuc["rows"][0]
    assert satir["orders"] == 4 and satir["spend"] == 125000 and satir["city"] == "Konya"
    assert not [c for c in api.calls if c[0] == "customers"]


async def test_nufus_taramasi_hizli_uc_yoksa_vendor_ucuna_duser() -> None:
    service, api, _ = _service()

    class Yok(Exception):
        code = "bbd_endpoint_missing"

    async def hizli(*_: Any, **__: Any) -> dict[str, Any]:
        raise Yok("uç yayında değil")

    api.bbd_customers = hizli  # type: ignore[attr-defined]
    await service._load_population()
    assert [c for c in api.calls if c[0] == "customers"]
