"""Kimlik senkronunun hata sözleşmesi.

Ayrı sınıflar var çünkü ÜÇ DURUM ÜÇ FARKLI EKRAN İSTER:

  · eşleşmemiş kurulum  → eşleme ekranı (kod girilir)
  · ağ yok              → "bu işlem için bağlantı gerekiyor" (deneme YAPILMAZ)
  · merkez reddetti     → merkezin cümlesi olduğu gibi gösterilir

Üçünü tek `RuntimeError` altında toplamak, kabuğun hangi ekranı açacağını
metinden çıkarmasını gerektirirdi.
"""

from __future__ import annotations

# Ekranda görünen cümle. TEK YERDE durur: aynı cümlenin yirmi panelde ayrı ayrı
# yazılması, biri değiştiğinde diğerlerinin eskimesi demekti (ADR 0021 §3).
CONNECTION_REQUIRED = "Bu işlem için bağlantı gerekiyor."


class IdentitySyncError(RuntimeError):
    """Kimlik senkronu sözleşmesi ihlali."""


class NotPaired(IdentitySyncError):
    """Kurulum henüz eşlenmemiş; kadro çekilemez, merkeze yazılamaz."""


class PairRejected(IdentitySyncError):
    """Merkez eşleme kodunu reddetti (süresi geçmiş, kullanılmış ya da yanlış).

    Sebep AYIRT EDİLMEZ: merkez de tek cümle döner.
    """


class WriteRequiresConnection(IdentitySyncError):
    """Yazma yalnız çevrimiçidir (ADR 0021 §3).

    Bu hata İSTEK GÖNDERİLMEDEN doğar. Yarım yazılmış bir kadro, hiç
    yazılmamıştan kötüdür; bağlantı yokken denemek de "belki gitmiştir"
    belirsizliğini üretirdi.
    """

    def __init__(self, message: str = CONNECTION_REQUIRED) -> None:
        super().__init__(message)
