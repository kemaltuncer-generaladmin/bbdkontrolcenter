"""Servis ayarları — tamamı ortam değişkeninden gelir.

K8 depoya sır konmasını yasaklar. Bu servis Coolify'da çalışır ve orada ayar
dosyası yerine ORTAM DEĞİŞKENİ vardır; `config/local.yaml` gibi bir git dışı
dosya kurulmaz. Değişken adları `KM_IDENTITY_` önekiyle ayrılır.

İKİ DEĞİŞKEN SESSİZ VARSAYILAN ALMAZ:

  · `KM_IDENTITY_PEPPER` yoksa servis AÇILMAZ. Pepper `secret_lookup`'ın sabit
    anahtarıdır; rastgele üretilip veritabanına yazılırsa bir kurtarma sonrası
    HERKESİN girişi bozulur ve bunu fark etmenin yolu yoktur.
  · `KM_IDENTITY_ADMIN_TOKEN` yoksa yönetim uçları 503 döner (bkz. `auth.py`).
    Boş token'ı "denetim kapalı" saymak, kurulum listesini ve eşleme kodu
    üretimini internete açık bırakırdı.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """Servis bu ayarla ayağa kalkamaz."""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise SettingsError(f"{name} bir tam sayı olmalı: {raw!r}") from error


@dataclass(slots=True, frozen=True)
class Settings:
    # Kalıcı disk Coolify'da bağlanır; imajın içine yazılmaz.
    db_path: str = "/data/identity.sqlite"
    # PIN arama hash'inin sabit anahtarı. KURULUMLARLA AYNI OLMAK ZORUNDA:
    # roster'daki `secret_lookup` bu anahtarla üretilir.
    pepper: str = ""
    # Yönetim uçlarının (eşleme kodu, kurulum listesi, iptal) tek anahtarı.
    admin_token: str = ""
    pair_code_ttl_seconds: int = 600
    # Kilit TTL'i ZORUNLUDUR (ADR 0020 §2): süresiz kilit, çöken bir istemcinin
    # kaydı sonsuza kapatması demektir.
    lock_default_ttl_seconds: int = 120
    lock_max_ttl_seconds: int = 900
    # İlk yöneticinin PIN'i. Yalnız veritabanı boşken kullanılır.
    bootstrap_pin: str = ""
    pin_min_length: int = 6
    max_failed_attempts: int = 5
    lockout_minutes: int = 15

    @staticmethod
    def from_env() -> Settings:
        pepper = os.environ.get("KM_IDENTITY_PEPPER", "").strip()
        if not pepper:
            raise SettingsError(
                "KM_IDENTITY_PEPPER tanımsız. Bu anahtar PIN arama hash'ini üretir ve "
                "kurulumlarla aynı olmak zorundadır; üretilip unutulamaz."
            )
        return Settings(
            db_path=os.environ.get("KM_IDENTITY_DB_PATH", "/data/identity.sqlite").strip()
            or "/data/identity.sqlite",
            pepper=pepper,
            admin_token=os.environ.get("KM_IDENTITY_ADMIN_TOKEN", "").strip(),
            pair_code_ttl_seconds=_int_env("KM_IDENTITY_PAIR_CODE_TTL_SECONDS", 600),
            lock_default_ttl_seconds=_int_env("KM_IDENTITY_LOCK_TTL_SECONDS", 120),
            lock_max_ttl_seconds=_int_env("KM_IDENTITY_LOCK_MAX_TTL_SECONDS", 900),
            bootstrap_pin=os.environ.get("KM_IDENTITY_BOOTSTRAP_PIN", "").strip(),
            pin_min_length=_int_env("KM_IDENTITY_PIN_MIN_LENGTH", 6),
            max_failed_attempts=_int_env("KM_IDENTITY_MAX_FAILED_ATTEMPTS", 5),
            lockout_minutes=_int_env("KM_IDENTITY_LOCKOUT_MINUTES", 15),
        )
