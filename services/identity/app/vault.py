"""Merkezin kasası — dağıtılacak sırlar veritabanında DÜZ DURMAZ (ADR 0025 §3).

Anahtar `KM_IDENTITY_VAULT_KEY` ortam değişkeninden gelir ve **veritabanına
yazılmaz**: aynı dosyada hem kilit hem anahtar tutmanın hiçbir karşılığı yok.
Coolify'da değişken olarak durur, yedeklerde durmaz; `/data/identity.sqlite`
sızarsa sırlar açılamaz.

**ANAHTAR YOKSA UÇ KAPALIDIR.** Bu dosyadaki tek karar budur ve `settings.py`
içindeki `KM_IDENTITY_ADMIN_TOKEN` kararının aynısıdır: eksik bir anahtarı
"şifreleme kapalı" sayıp düz metne düşmek, sunucu parolalarını bir gün sessizce
açıkta bırakırdı. Kapalı olduğunu SÖYLEYEN bir kapı (503), sessizce açık bir
kapıdan iyidir.

**BOZUK ANAHTAR DA KAPALIDIR.** Fernet anahtarı 32 baytın urlsafe-base64
kodudur; elle girilen bir parola bu biçimde değildir ve `Fernet(...)` kurucusu
patlar. O hatayı isteğin içine bırakmak 500 üretirdi; burada yakalanır, loga
düşer ve uç yine 503 der — ikisinin de karşılığı "anahtarı düzelt"tir.

Fernet (AES-128-CBC + HMAC) seçildi çünkü `km_platform/secrets/vault.py` de
onu kullanıyor: iki tarafta iki ayrı şifreleme şeması tutmanın bir faydası yok.
`cryptography` bu servis için `services/identity/requirements.txt` içinde AYRICA
ilan edilir (K11) — çekirdeğin bağımlılığı olması, kabın onu kurduğu anlamına
gelmez.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from cryptography.fernet import Fernet, InvalidToken

from .settings import VAULT_KEY_ENV

log = structlog.get_logger("identity.vault")

VAULT_KEY_MISSING = (
    "Kasa anahtarı tanımsız; kurulum paketi uçları kapalı "
    f"({VAULT_KEY_ENV})."
)
VAULT_KEY_BROKEN = (
    "Kasa anahtarı okunamadı; kurulum paketi uçları kapalı "
    f"({VAULT_KEY_ENV} geçerli bir Fernet anahtarı olmalı)."
)


class VaultUnavailable(RuntimeError):
    """Kasa açılamadı. Çağıran bunu 503'e çevirir — 500'e değil.

    Ayrımı korumak önemli: 500 "kod patladı" der ve kimse ne yapacağını
    bilmez; 503 "bu uç şu an kapalı" der ve nedeni cümlenin içindedir.
    """


class VaultCorrupt(RuntimeError):
    """Satır bu anahtarla çözülemedi — anahtar değişmiş olabilir.

    Sessizce `None` dönmek, eksik bir sırrı "hiç konmamış" gibi gösterirdi:
    kurulum paketi yarım gider, geçit çalışmaz ve kimse sebebini göremez.
    """


def build_cipher(key: str) -> Fernet:
    """Anahtardan şifreleyiciyi kurar. Yoksa/bozuksa `VaultUnavailable`."""
    if not key:
        raise VaultUnavailable(VAULT_KEY_MISSING)
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as error:
        # Anahtarın KENDİSİ loga yazılmaz; yalnız biçiminin bozuk olduğu.
        log.error("kasa anahtarı geçersiz biçimde", error=str(error))
        raise VaultUnavailable(VAULT_KEY_BROKEN) from error


def encrypt(cipher: Fernet, value: Any) -> str:
    """Değeri JSON'a çevirip şifreler.

    **JSON ARA KATMANI ŞART.** Dağıtılan şey yalnız metin değil: modül ayarları
    arasında `read_only: false` gibi mantıksal değerler var ve hepsini metne
    çevirip geri okumak `"false"` ile `False`'ı ayırt edemez hâle getirirdi
    (`km_core/config/settings_store.py` aynı gerekçeyle JSON saklar).
    """
    payload = json.dumps(value, ensure_ascii=False)
    return cipher.encrypt(payload.encode("utf-8")).decode("ascii")


def decrypt(cipher: Fernet, blob: str) -> Any:
    """Şifreli değeri çözer ve JSON'dan geri okur."""
    try:
        plain = cipher.decrypt(blob.encode("utf-8"))
    except InvalidToken as error:
        raise VaultCorrupt(
            "Kurulum paketindeki bir değer çözülemedi — kasa anahtarı değişmiş olabilir."
        ) from error
    return json.loads(plain.decode("utf-8"))
