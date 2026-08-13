"""Kantin kart QR'ı — kantinin şemasıyla BİT-UYUMLU kodlayıcı.

Kaynak sözleşme: bbd-kantin → `backend/app/Domain/Qr/QrCardCodec.php`
(ve Android eşi `QrCodec.kt`). Aynı kart hem burada üretilebilmeli hem kasada
okunabilmeli.

    düz metin = {"v":1,"sid":"<opak-id>","iat":<epoch-sn>}      (UTF-8, boşluksuz)
    AES-256-GCM · nonce 12 bayt · etiket 128 bit · AAD = [sürüm baytı]
    bayt düzeni = sürüm(1) ‖ nonce(12) ‖ şifreli ‖ etiket(16)
    qrText = base64url, padding YOK

Alan sırası önemlidir: PHP `json_encode` alanları verildiği sırada yazar.
Sıra değişirse kasa şifreyi çözer ama beklediği metni bulamaz.

Anahtar burada ÜRETİLMEZ: kasanın anahtarıdır, `canteen.api` üzerinden gelir
ve arayüze hiç inmez.
"""

from __future__ import annotations

import base64
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
NONCE_LEN = 12
TAG_LEN = 16


def encode(opaque_id: str, key: bytes, issued_at: int | None = None) -> str:
    if len(key) != 32:
        raise ValueError("QR anahtarı 32 bayt (AES-256) olmalı.")

    issued_at = issued_at if issued_at is not None else int(time.time())
    plaintext = json.dumps(
        {"v": VERSION, "sid": opaque_id, "iat": issued_at},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    nonce = os.urandom(NONCE_LEN)
    sealed = AESGCM(key).encrypt(nonce, plaintext, bytes([VERSION]))
    raw = bytes([VERSION]) + nonce + sealed
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(qr_text: str, key: bytes) -> dict[str, object] | None:
    """Çözer; bize ait değilse None döner (kasa da böyle davranır)."""
    try:
        padded = qr_text + "=" * (-len(qr_text) % 4)
        raw = base64.urlsafe_b64decode(padded)
        if len(raw) < 1 + NONCE_LEN + TAG_LEN or raw[0] != VERSION:
            return None
        plain = AESGCM(key).decrypt(raw[1:1 + NONCE_LEN], raw[1 + NONCE_LEN:], bytes([VERSION]))
        data = json.loads(plain)
        return data if isinstance(data, dict) and "sid" in data else None
    except Exception:  # noqa: BLE001 — bozuk/yabancı kart sessizce reddedilir
        return None
