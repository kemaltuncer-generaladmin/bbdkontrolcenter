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


def test_nihai_durumdan_donus_yok() -> None:
    # İptal edilmiş sipariş para ve stok hareketi doğurmuş olabilir; onu
    # `processing`e çekmek iade edilmiş bir siparişi yeniden kargoya hazır
    # gösterirdi.
    block = ord_.status_block(_row("canceled"), "processing")
    assert "nihai" in block
    assert "yeni sipariş" in block


def test_tamamlanmis_siparis_geri_cekilemez() -> None:
    # Geri çekmek listeyi düzeltmez, "kargoya hazır"a ikinci kez düşürür.
    block = ord_.status_block(_row("completed"), "processing")
    assert "geçilemez" in block
    # Hangi geçişlerin mümkün olduğu SÖYLENİR: "olmaz" demek tek başına
    # kullanıcıyı ekranda dolaştırır.
    assert "Kapandı" in block


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


def test_ayni_duruma_gecis_is_degildir() -> None:
    assert "zaten" in ord_.status_block(_row("processing"), "processing")
