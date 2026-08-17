"""Kimlik senkronunun hata sözleşmesi.

Ayrı sınıflar var çünkü HER DURUM AYRI BİR EKRAN İSTER:

  · eşleşmemiş kurulum  → eşleme ekranı (kod girilir)
  · ağ yok              → "bu işlem için bağlantı gerekiyor" (deneme YAPILMAZ)
  · merkez reddetti     → merkezin cümlesi olduğu gibi gösterilir
  · yönetim anahtarı yok → ekran açılır, yalnız yönetim düğmeleri kapanır

Hepsini tek `RuntimeError` altında toplamak, kabuğun hangi ekranı açacağını
metinden çıkarmasını gerektirirdi.
"""

from __future__ import annotations

# Ekranda görünen cümle. TEK YERDE durur: aynı cümlenin yirmi panelde ayrı ayrı
# yazılması, biri değiştiğinde diğerlerinin eskimesi demekti (ADR 0021 §3).
CONNECTION_REQUIRED = "Bu işlem için bağlantı gerekiyor."

# Kasada `identity_sync.admin_token` yokken kurulum yönetimi hiç denenmez.
MANAGEMENT_KEY_REQUIRED = (
    "Merkezin yönetim anahtarı bu kurulumun kasasında yok "
    "(identity_sync.admin_token)."
)


class IdentitySyncError(RuntimeError):
    """Kimlik senkronu sözleşmesi ihlali."""


class NotPaired(IdentitySyncError):
    """Kurulum henüz eşlenmemiş; kadro çekilemez, merkeze yazılamaz."""


class PairRejected(IdentitySyncError):
    """Merkez eşleme kodunu reddetti (süresi geçmiş, kullanılmış ya da yanlış).

    Sebep AYIRT EDİLMEZ: merkez de tek cümle döner.
    """


class ManagementKeyMissing(IdentitySyncError):
    """Merkezin yönetim anahtarı kasada yok.

    Kurulum listesi, kod üretimi ve iptal merkezde `require_admin` ile
    korunuyor; o anahtar bu kurulumun kasasında durur (K8) ve ayara yazılmaz.
    Anahtar yokken ekran EŞLEME DURUMUNU YİNE GÖSTERİR, yalnız yönetim
    düğmelerini nedenini yazarak kapatır — patlayan bir ekran, eksik bir
    anahtarın karşılığı olamaz (K7).
    """

    def __init__(self, message: str = MANAGEMENT_KEY_REQUIRED) -> None:
        super().__init__(message)


class WriteRequiresConnection(IdentitySyncError):
    """Yazma yalnız çevrimiçidir (ADR 0021 §3).

    Bu hata İSTEK GÖNDERİLMEDEN doğar. Yarım yazılmış bir kadro, hiç
    yazılmamıştan kötüdür; bağlantı yokken denemek de "belki gitmiştir"
    belirsizliğini üretirdi.
    """

    def __init__(self, message: str = CONNECTION_REQUIRED) -> None:
        super().__init__(message)
