"""ELLE DURUM DEĞİŞTİRME — Bagisto'nun türettiği durumun üstüne konan istisna.

Durum normalde TÜRETİLİR: fatura kesilince `processing`, kargolanınca
`completed`. Bu yol yalnız istisnalar içindir ve iki şeyi korumak zorundadır:

  · GEÇİŞ MATRİSİ — nihai durumlardan dönüş yok, `completed`'dan geri yok.
    Geri dönüş, kargolanmış bir siparişi "kargoya hazır" listesine ikinci kez
    düşürür ve aynı paket iki kez gönderilir.
  · İPTAL KAPISI — `canceled` bu uçtan hedef DEĞİLDİR. İptalin ayrı izin
    anahtarı ve süre penceresi var; buradan kabul etmek, iptal izni olmayan
    personele durum düğmesinden iptal ettirirdi (K9/K10).
"""

from __future__ import annotations

from typing import Any

from store_orders_backend import orders as ord_

GEREKCE = "Durum elle duzeltiliyor, magaza disinda halledildi"


def _row(status: str) -> dict[str, Any]:
    return {"status": status, "orderNo": "26"}


# =========================================================== geçiş matrisi

def test_gecerli_gecis_engellenmez() -> None:
    assert ord_.status_block(_row("processing"), "completed") == ""


def test_donmus_kaynaktan_gecis_yok() -> None:
    # İptal akışı stoğu iade etmiş ve bankaya iptal göndermiş olabilir; durum
    # sütununu geri çevirmek bunların hiçbirini geri almaz.
    block = ord_.status_block(_row("canceled"), "processing")
    assert "geri açılmaz" in block
    assert ord_.status_targets(_row("canceled")) == ()


def test_kapali_ve_sahte_supheli_de_donmustur() -> None:
    for durum in ("closed", "fraud"):
        assert ord_.status_targets(_row(durum)) == ()
        assert ord_.status_block(_row(durum), "processing") != ""


def test_tamamlanmis_siparis_geri_cekilebilir() -> None:
    """Mağaza sözleşmesi bunu YAZILABİLİR sayıyor; ekran daraltmaz.

    Bir ara burada "geri çekilemez" kuralı vardı ve gerekçesi "kargoya hazır
    listesine ikinci kez düşer" idi. Yanlıştı: o liste kalan adede bakıyor,
    tamamı kargolanmış sipariş zaten süzülüyor. Ekranı sunucudan DAR tutmak,
    yapılabilecek bir işi gizlemek olurdu.
    """
    assert ord_.status_block(_row("completed"), "processing") == ""


def test_kilitleyen_hedefler_yazilamaz() -> None:
    # `closed` ve `fraud` fatura/gönderi/iptal/iade kapılarının dördünü birden
    # kilitler; tek yazım hatası siparişi kalıcı olarak kilitlerdi.
    for durum in ("closed", "fraud"):
        block = ord_.status_block(_row("processing"), durum)
        assert "kilitler" in block
        assert durum not in ord_.status_targets(_row("processing"))


def test_iptal_bu_uctan_yapilamaz_ve_nedeni_soylenir() -> None:
    block = ord_.status_block(_row("processing"), "canceled")
    assert "İptal et" in block
    # "Geçilemez" demek, iptalin hiç mümkün olmadığı sonucuna götürürdü.
    assert "geçilemez" not in block


def test_bilinmeyen_durum_reddedilir() -> None:
    # Serbest metin kabul etmek, `complete` yazan kullanıcıyı hiçbir ekranda
    # görünmeyen bir duruma düşürürdü.
    block = ord_.status_block(_row("processing"), "complete")
    assert "Bilinmeyen durum" in block


def test_ekran_matrisi_MAGAZA_sozlesmesiyle_ayni() -> None:
    """Sözleşmenin sahibi mağaza (`OrderStatusTransition::writableTargets`).

    Ekran geniş olursa kullanıcıya 409 aldırır, dar olursa yapılabilecek bir
    işi gizler. Bu test iki listenin ayrışmasını yakalar.
    """
    assert set(ord_.STATUS_WRITABLE) == {"pending", "pending_payment", "processing",
                                         "completed"}
    assert set(ord_.STATUS_REFUSED) == {"canceled", "closed", "fraud"}
    assert set(ord_.STATUS_FROZEN) == {"canceled", "closed", "fraud"}


def test_ayni_duruma_gecis_is_degildir() -> None:
    assert "zaten" in ord_.status_block(_row("processing"), "processing")
