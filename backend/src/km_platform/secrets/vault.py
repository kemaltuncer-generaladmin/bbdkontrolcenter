"""Sır kasası — `secrets` platform yeteneği.

K8: depoda sır bulunmaz. Kasa iki kaynaktan okur:

  1. `config/local.yaml` → `secrets.<anahtar>`   (git dışı, elle girilen sırlar)
  2. çekirdek deposundaki `secrets` tablosu       (çalışırken üretilenler)

Tabloya yazılan değerler anahtar dosyasıyla şifrelenir. Anahtar dosyası
`data/secret.key` altında, 0600 izinle durur ve depoya girmez.

Şifreleme Fernet (AES-128-CBC + HMAC) — `cryptography` zaten bağımlılıkta
(argon2/asyncssh üzerinden gelir).

## Anahtar sunucuda ORTAM DEĞİŞKENİNDEN gelir (`KM_SECRET_KEY`)

Konteynerde `/data` ilk açılışta boştur. Anahtar yalnız dosyadan okunsaydı
kasa YENİ BİR ANAHTAR ÜRETİR ve veritabanındaki şifreli sırların hiçbirini
çözemezdi — `core.pin_pepper` dahil. Belirti "kimse giriş yapamıyor" olurdu ve
sebebi (anahtar değişti) hiç ele vermezdi; PIN'ler doğru, kadro dolu, log
temiz görünürdü.

`KM_SECRET_KEY` tanımlıysa dosyaya HİÇ BAKILMAZ ve dosya YAZILMAZ: sır
Coolify'ın kasasında kalır, konteynerin diskine düşmez.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from km_core.config.loader import Config
from km_core.store.base import StoreLike

#: Kasa anahtarını taşıyan ortam değişkeni. Tanımlıysa dosyaya bakılmaz.
SECRET_KEY_ENV = "KM_SECRET_KEY"


class Vault:
    def __init__(self, store: StoreLike, config: Config) -> None:
        self._store = store
        self._config = config
        self._key_path: Path = config.path("core.secret_key_path", "data/secret.key")
        self._fernet: Fernet | None = None

    async def open(self) -> None:
        self._fernet = Fernet(self._load_or_create_key())

    # ------------------------------------------------------------- anahtar

    def _load_or_create_key(self) -> bytes:
        # Ortam değişkeni EN ÜSTTE. Sunucuda anahtar Coolify kasasından gelir;
        # dosyaya düşmez ve her dağıtımda aynı kalır (bkz. modül başlığı).
        from_env = os.environ.get(SECRET_KEY_ENV, "").strip()
        if from_env:
            try:
                Fernet(from_env.encode("ascii"))
            except (ValueError, TypeError) as error:
                # Bozuk anahtarla açılmak, "sır çözülemedi" hatalarını
                # veritabanına ya da PIN'lere yıkardı. Açılışta patlamak yeğdir.
                raise ValueError(
                    f"{SECRET_KEY_ENV} geçerli bir Fernet anahtarı değil: {error}"
                ) from error
            return from_env.encode("ascii")

        if self._key_path.is_file():
            return self._key_path.read_bytes().strip()

        key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        # Önce dar izinle oluştur, sonra yaz: dosya bir an bile herkese açık kalmasın.
        descriptor = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key

    # ---------------------------------------------------------------- okuma

    async def get(self, key: str) -> str | None:
        """Sırrı getirir. Önce local.yaml, sonra kasa tablosu.

        Ayar dosyasında iki yazım da geçerlidir — noktalı anahtar tek parça
        olarak da, iç içe blok olarak da yazılabilir:

            secrets:
              canteen.enrollment_secret: "…"     # düz
            secrets:
              canteen:
                enrollment_secret: "…"           # iç içe
        """
        from_config = self._config.section("secrets").get(key)
        if from_config in (None, ""):
            from_config = self._config.get(f"secrets.{key}")
        if from_config not in (None, ""):
            return str(from_config)

        row = await self._store.fetch_one("SELECT value FROM secrets WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            return self._cipher.decrypt(row["value"].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Anahtar değişmiş: değer okunamaz. Sessizce None dönmek yanlış olur.
            raise RuntimeError(f"'{key}' sırrı çözülemedi — kasa anahtarı değişmiş olabilir.") from None

    async def set(self, key: str, value: str) -> None:
        encrypted = self._cipher.encrypt(value.encode("utf-8")).decode("utf-8")
        await self._store.execute(
            "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, encrypted),
        )

    async def delete(self, key: str) -> None:
        await self._store.execute("DELETE FROM secrets WHERE key = ?", (key,))

    async def keys(self) -> list[str]:
        """Kasadaki anahtar ADLARI — değerler değil (`secrets.view` bunu görür)."""
        rows = await self._store.fetch_all("SELECT key FROM secrets ORDER BY key")
        return [row["key"] for row in rows]

    #: Pepper'ın KENDİLİĞİNDEN üretildiğini söyleyen işaret.
    #
    # NEDEN GEREKLİ. Merkeze eşlenen bir kurulum, merkezin pepper'ını almak
    # ZORUNDADIR (`roster_projection` — kadro `secret_lookup` taşır). Ama
    # eşleme, gerçek kullanıcısı olan bir kurulumun pepper'ını EZEMEZ: ezerse o
    # makinedeki herkesin girişi bir anda kırılır ve düz PIN'ler hiçbir yerde
    # saklanmadığı için geri getirilemez.
    #
    # İkisini ayırmanın tek yolu, değerin nereden geldiğini bilmektir: taze bir
    # kurulumda pepper açılışta rastgele doğar ve o an hiçbir kullanıcıyı
    # bağlamaz — ezilmesi zararsızdır. Elle konmuş ya da eşlemeyle benimsenmiş
    # bir pepper ise kullanıcılara bağlıdır ve dokunulmaz.
    AUTO_FLAG = "core.pin_pepper_auto"

    async def pepper(self) -> str:
        """PIN arama hash'inin sabit anahtarı. Veritabanında değil, kasada durur."""
        value = await self.get("core.pin_pepper")
        if value is None:
            value = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            await self.set("core.pin_pepper", value)
            await self.set(self.AUTO_FLAG, "1")
        return value

    async def pepper_is_auto(self) -> bool:
        """Pepper kendiliğinden mi doğdu? (bkz. `AUTO_FLAG`)"""
        return await self.get(self.AUTO_FLAG) == "1"

    async def adopt_pepper(self, value: str) -> None:
        """Merkezin pepper'ını benimser ve 'kendiliğinden' işaretini kaldırır."""
        await self.set("core.pin_pepper", value)
        await self.delete(self.AUTO_FLAG)

    @property
    def _cipher(self) -> Fernet:
        if self._fernet is None:
            raise RuntimeError("Kasa açılmadı.")
        return self._fernet
