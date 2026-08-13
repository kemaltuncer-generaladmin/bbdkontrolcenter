"""`channel` süzgeci GET sorgularından düşürülür.

BULUNAN HATA (2026-08-14). Bagisto'nun admin API'si `channel` parametresini
uçtan uca tutarsız yorumluyor ve yanlış biçimde HATA VERMİYOR — sessizce boş
sonuç döndürüyor. Canlıda ölçüldü (mağazada tek kanal: id=1, code="default"):

    uç                  kanal yok    channel=default    channel=1
    orders                     18            **0**             18
    dashboard/stats        35.062 ₺      35.062 ₺          **0**
    reporting/stats        35.062 ₺      35.062 ₺          **0**
    products                 1422          1422          **bozuk**

Sipariş KİMLİK bekliyor, pano ve rapor KOD bekliyor. Modüllerin ayarında
`channel: "default"` yazılı olduğu için sipariş listesi ve ona dayanan her
rakam boş dönüyordu: HTTP 200, hata yok, sadece sıfır.

Bu testin koruduğu şey tek tek çağrılar değil KURAL: yeni bir uç eklenip
`channel` süzgeci geçirildiğinde de yakalar.
"""

from __future__ import annotations

from store_api_backend.client import _drop_channel  # type: ignore[import-not-found]


def test_kanal_get_sorgusundan_dusurulur() -> None:
    # Sessiz sıfırın kaynağı: modül ayarındaki "default" doğrudan süzgece
    # geçiyordu. Kanal gitmeyince Bagisto tek kanalı zaten kendisi seçiyor.
    kalan = _drop_channel("/api/admin/orders", {"channel": "default", "status": "processing"})
    assert kalan == {"status": "processing"}


def test_kanal_yoksa_sozluk_oldugu_gibi_kalir() -> None:
    param = {"status": "processing", "page": 2}
    assert _drop_channel("/api/admin/orders", param) == param


def test_bos_parametre_bozulmaz() -> None:
    assert _drop_channel("/api/admin/orders", None) is None
    assert _drop_channel("/api/admin/orders", {}) == {}


def test_ayar_ucunda_kanal_KORUNUR() -> None:
    # `core_config` değerleri gerçekten kanal başına saklanır; oradan kanalı
    # düşürmek "hangi kanalın ayarı" sorusunu belirsizleştirirdi. Tek istisna.
    param = {"channel": "default", "slug": "general.content"}
    assert _drop_channel("/api/admin/settings/configuration", param) == param


def test_dusurme_cagirana_ait_sozlugu_DEGISTIRMEZ() -> None:
    # Geçit aynı süzgeç sözlüğünü sayfalama döngüsünde yeniden kullanıyor;
    # yerinde silme yapılsaydı ilk sayfa kanalı görür, sonrakiler görmezdi —
    # sayfalar arası tutarsızlık, üstelik yalnız çok sayfalı sonuçlarda.
    param = {"channel": "default", "status": "processing"}
    _drop_channel("/api/admin/orders", param)
    assert param == {"channel": "default", "status": "processing"}
