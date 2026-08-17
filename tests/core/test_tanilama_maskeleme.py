"""Tanılama maskelemesi (K8).

DESTEK PAKETİ DIŞARI ÇIKAR: e-postaya, sohbete, hata kaydına. İçindeki
günlükte bu depoda gerçekten bulunan şeyler var — sunucu adresleri, `Bearer`
başlıkları, veli telefonları. Maskeleme bu yüzden isteğe bağlı değildir.

İKİ YÖNLÜ SINAMA. Yalnız "sır gitti mi" diye bakmak yetmez: her şeyi yıldıza
çeviren bir süzgeç de o testi geçer ve günlüğü okunamaz hâle getirirdi. Bu
yüzden aşağıda hem GİDENİ hem KALANI sınıyoruz — satırın ne anlattığı okunur
kalmalı, kimliği okunmamalı.
"""

from __future__ import annotations

import pytest

from km_core.http.masking import HOST_MARK, mask_text, mask_value

# Depoda gerçekten duran biçimler (config/local.yaml). Değerler test için
# kısaltılmadı: eşik uzunluğu değişirse test bunu yakalasın.
BELIRTEC = "6b30d6118a3a6ebfe586fc8b0c6bb70ca695899daaa862d5ac903b3ccb9e5961"
MAGAZA_TOKEN = "1|3EjaOnr1b8ioTubRL6LzxzyJZaHfnYRSkIPPEr6X"


@pytest.mark.parametrize(
    ("metin", "gitmeli"),
    [
        ("GET https://bbdstore.com.tr/api/bell/status", "bbdstore.com.tr"),
        ("base_url: https://api.benimlezzetdunyam.com.tr", "benimlezzetdunyam"),
        ("sunucuya bağlanıldı: 78.47.241.163", "78.47.241.163"),
        ("bell.bridge_token: 49fe6dcc5648c8929fc6706655be965caf2f5638d2b1f3da",
         "49fe6dcc5648c8929fc6706655be965caf2f5638d2b1f3da"),
        (f"Authorization: Bearer {MAGAZA_TOKEN}", MAGAZA_TOKEN),
        (f"control_secret={BELIRTEC}", BELIRTEC),
        ("veli telefonu 0532 123 45 67 ile arandı", "0532 123 45 67"),
        ("veli telefonu 5321234567", "5321234567"),
        ("gonderen: veysel.kemal@ornek.com", "veysel.kemal@ornek.com"),
    ],
)
def test_sir_disari_cikmaz(metin: str, gitmeli: str) -> None:
    assert gitmeli not in mask_text(metin)


def test_ozel_anahtar_blogu_tumuyle_gider() -> None:
    metin = (
        "private_key: -----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCcqH5zlbEaAxEi\n"
        "gwgIKubdpuu0mioLmcwSbu/tNDhbUNYy+3cWTz5/3aEXtnq8vr/aoqf/EcLV3wqY\n"
        "-----END PRIVATE KEY-----"
    )
    maskeli = mask_text(metin)

    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCcqH5zlbEaAxEi" not in maskeli
    assert "BEGIN" in maskeli, "blok tümüyle silinmiş; okuyan ne olduğunu anlamalı"


def test_satirin_anlami_korunur() -> None:
    """Maskeleme BİÇİMİ bırakır, İÇERİĞİ alır."""
    maskeli = mask_text('HTTP Request: GET https://bbdstore.com.tr/api/bell/status "200 OK"')

    assert "HTTP Request: GET" in maskeli
    assert "/api/bell/status" in maskeli, "yol da gitmiş; günlük okunamaz hâle geldi"
    assert "200 OK" in maskeli
    assert HOST_MARK in maskeli


def test_dongu_adresi_maskelenmez() -> None:
    """127.0.0.1 bir sır değil, bu uygulamanın mimarisi (ADR 0002)."""
    maskeli = mask_text("çekirdeğe bağlanılamadı: http://127.0.0.1:8787/health")

    assert "127.0.0.1:8787" in maskeli
    assert "/health" in maskeli


def test_siradan_gunluk_satiri_bozulmaz() -> None:
    """Aşırı maskeleme de bir arızadır: tanılama okunamazsa işe yaramaz."""
    satir = "12:53:57 [info     ] köprü ses kitaplığı eşitlendi  removed=1 total=10 uploaded=0"

    assert mask_text(satir) == satir


def test_belirtec_bulunamadi_cumlesi_maskelenmez() -> None:
    """`token` kelimesinin geçmesi, ardındaki her şeyin sır olduğu anlamına gelmez."""
    satir = "belirteç yok: token bulunamadı, oturum açılmadı"

    assert mask_text(satir) == satir


def test_agac_dolasilir() -> None:
    """Özet ve ayar listesi sözlük olarak gidiyor; metinler orada da maskelenir."""
    maskeli = mask_value(
        {"adres": "https://bbdstore.com.tr", "liste": ["0532 123 45 67"], "sayi": 3},
    )

    assert "bbdstore.com.tr" not in str(maskeli)
    assert "5321234567" not in str(maskeli).replace(" ", "")
    assert maskeli["sayi"] == 3, "sayı bozulmuş"


def test_bos_metin_calisir() -> None:
    assert mask_text("") == ""
