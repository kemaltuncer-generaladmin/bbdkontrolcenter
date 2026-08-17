"""Cihaz eşlemesi (ADR 0021 §4).

Akış — KDS kasalarında çalışan desenin aynısı:

  1. Yönetici **tek kullanımlık, süreli** kod üretir (`create_pair_code`).
  2. Yeni kurulum ilk açılışta kodu girer, kendi anahtar çiftini üretmiş
     olarak açık anahtarını gönderir (`pair`).
  3. Karşılığında kurulum token'ı alır; token yalnızca O AN döner, sonra
     veritabanında yalnız sha256'sı kalır.
  4. Token iptal edilebilir (`revoke`); iptal edilen kurulum kadro çekemez.

KOD DA HASH'LENEREK SAKLANIR. Süreli ve tek kullanımlık olması, veritabanını
okuyabilen birinin bekleyen bir kodu alıp kendi makinesini eşlemesini
engellemez — hash engeller.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from km_core.store.db import Store

from .auth import constant_time_equals, hash_token

# Kod insan tarafından yazılır: rakam, sekiz hane, ekranda 4+4 gösterilir.
# Harf karıştırmak (O/0, I/1) telefonla okunurken hata üretiyordu.
PAIR_CODE_DIGITS = 8


class PairError(RuntimeError):
    """Eşleme reddedildi. Sebep AYIRT EDİLMEZ — dışarıya tek cümle çıkar."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_pair_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_DIGITS))


async def create_pair_code(store: Store, *, ttl_seconds: int, note: str | None = None) -> dict[str, Any]:
    """Yeni kod üretir ve BEKLEYEN ESKİ KODLARI GEÇERSİZ KILAR.

    Aynı anda birden fazla canlı kod tutmanın hiçbir karşılığı yok: yönetici
    ekranda tek kod görüyor, sahadaki makine tek kod giriyor. Eskisi geçerli
    kalsaydı, "kodu yeniledim" diyen yöneticinin arkasında hâlâ çalışan bir kod
    kalırdı ve o kodun varlığı hiçbir ekranda görünmezdi. KDS kasalarında da
    yeni kod eskisini geçersiz kılıyor; desen oradan geliyor.

    Eski satır SİLİNMEZ ve "kullanıldı" da denmez — SÜRESİ BURADA BİTİRİLİR.
    `used_at` işaretlemek, hiç kullanılmamış bir kodu kullanılmış gibi
    gösterip denetimdeki "bu kod kimde yandı" sorusunu yanlış yanıtlardı.
    """
    now = datetime.now(UTC)
    stamp = now.isoformat(timespec="seconds")
    await store.execute(
        "UPDATE pair_codes SET expires_at = ? WHERE used_at IS NULL AND expires_at > ?",
        (stamp, stamp),
    )

    code = generate_pair_code()
    expires = now + timedelta(seconds=ttl_seconds)
    await store.execute(
        "INSERT INTO pair_codes (code_hash, created_at, expires_at, note) VALUES (?, ?, ?, ?)",
        (hash_token(code), _now(), expires.isoformat(timespec="seconds"), note),
    )
    # KOD YALNIZ BURADA DÜZ GÖRÜNÜR. Yönetici ekranına düşer, veritabanına
    # düşmez.
    return {"code": code, "expiresAt": expires.isoformat(timespec="seconds")}


async def _assert_code_usable(store: Store, digest: str) -> None:
    """Kod var mı ve süresi geçmemiş mi? Süresi geçmiş, kullanılmış ve hiç var
    olmayan kod AYNI hatayı verir.

    BU ÖN DENETİM TEK BAŞINA YETMEZ ve yetmediği bilinerek yazılmıştır: iki
    istek arasında kodu kim yakarsa yakar, burada ikisi de "kullanılabilir"
    görür. Yarışı bitiren tek şey `_consume_code` içindeki koşullu UPDATE'tir.
    Bu adım yalnızca SÜRE bilgisini okumak ve karşılaştırmayı SABİT SÜREDE
    yapmak içindir: `hash_token` tuzsuz bir sha256'dır ve eşleşmeyi SQL
    eşitliğine bırakmak, kodu harf harf tahmin etmeye zaman sızdırırdı.
    """
    rows = await store.fetch_all(
        "SELECT code_hash, expires_at, used_at FROM pair_codes WHERE used_at IS NULL"
    )
    matched: dict[str, Any] | None = None
    for row in rows:
        # DÖNGÜ ERKEN KIRILMAZ — `auth.require_installation` ile aynı gerekçe.
        if constant_time_equals(str(row["code_hash"]), digest):
            matched = row
    if matched is None:
        raise PairError("Eşleme kodu geçersiz.")
    if datetime.fromisoformat(str(matched["expires_at"])) < datetime.now(UTC):
        raise PairError("Eşleme kodu geçersiz.")


async def _claim_code(store: Store, digest: str, installation_id: str) -> None:
    """Kodu ATOMİK olarak bu kurulumun adına yakar.

    **KOŞUL UPDATE'İN İÇİNDEDİR.** Eskiden `used_at IS NULL` yalnızca önceki
    SELECT'te aranıyor, UPDATE ise `WHERE code_hash = ?` ile koşulsuz
    yazıyordu; aynı kodu taşıyan iki `/pair` isteği aradaki `await` noktasında
    birbirine giriyor, ikisi de boş satırı görüyor ve TEK KULLANIMLIK kod iki
    makineyi birden eşleyebiliyordu. Koşul artık yazmanın kendisindedir: SQLite
    tek satırlık UPDATE'i bölünmez uygular, ikinci istek 0 satır etkiler ve
    reddedilir.

    `used_by` de AYNI ifadede yazılır — ayrı bir UPDATE'e bırakmak, o adım
    patlarsa "bu kod kimde yandı" sorusunu cevapsız bırakırdı.
    """
    async with store.db.execute(
        "UPDATE pair_codes SET used_at = ?, used_by = ? "
        "WHERE code_hash = ? AND used_at IS NULL",
        (_now(), installation_id, digest),
    ) as cursor:
        affected = cursor.rowcount
    await store.db.commit()
    if affected == 0:
        # Yarışı kaybettik ya da kod aradan yakıldı. Sebep AYIRT EDİLMEZ.
        raise PairError("Eşleme kodu geçersiz.")


async def _release_code(store: Store, digest: str, installation_id: str) -> None:
    """Yakılmış kodu geri verir — YALNIZ HÂLÂ BİZİMSE.

    `used_by` koşulu şart: aradan başka bir istek kodu yeniden kapmışsa onun
    hakkını elinden almayız.
    """
    await store.execute(
        "UPDATE pair_codes SET used_at = NULL, used_by = NULL "
        "WHERE code_hash = ? AND used_by = ?",
        (digest, installation_id),
    )


async def pair(store: Store, *, code: str, public_key: str, machine_name: str,
               platform: str, version: str) -> dict[str, Any]:
    """Kodu tüketir, kurulumu kaydeder ve token'ı TEK SEFER döndürür.

    **KOD, KURULUM SATIRI YAZILAMAZSA GERİ VERİLİR.** Eskiden kod yakılıyor ve
    kurulum satırı yazılmasa bile yanık kalıyordu; INSERT patladığında (kısıt
    ihlali, dolu disk) sahadaki makine tek kullanımlık kodunu sessizce
    kaybediyordu. Artık başarısız yazma kodu serbest bırakır.

    **NEDEN TEK BİR İŞLEM (TRANSACTION) DEĞİL.** `Store` tek bağlantı tutar ve
    her ifadeyi kendi başına commit eder; sidecar da servis de tek süreçtir.
    İkisini elle açılmış bir işleme almayı denedik ve test tam da bunu yakaladı:
    yarışı KAYBEDEN isteğin `rollback()` çağrısı, aynı bağlantıyı paylaştığı
    için KAZANAN isteğin henüz commit edilmemiş satırlarını da siliyordu —
    kurulum listesi boş kalıyordu. Bağlantı paylaşıldığı sürece işlem sınırı
    isteğe ait olamaz; bu yüzden geri alma, işlem değil TELAFİ EDİCİ bir
    yazmadır (`_release_code`).

    Kalan tek boşluk, iki yazma arasında sürecin ölmesidir; o durumda kod yanık
    kalır ve yönetici yeni kod üretir. Eski davranışta bu boşluk sürecin
    ölmesini bile gerektirmiyordu.
    """
    digest = hash_token(code)
    await _assert_code_usable(store, digest)

    installation_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)

    await _claim_code(store, digest, installation_id)
    try:
        await store.execute(
            "INSERT INTO installations (id, machine_name, platform, version, public_key, "
            "token_hash, status, paired_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                installation_id, machine_name, platform, version, public_key,
                hash_token(token), _now(), _now(),
            ),
        )
    except Exception:
        await _release_code(store, digest, installation_id)
        raise
    return {"installationId": installation_id, "token": token}


def entry(row: dict[str, Any]) -> dict[str, Any]:
    """Kurulum listesi kaydı. `token_hash` ve `public_key` DIŞARI ÇIKMAZ."""
    return {
        "id": row["id"],
        "machineName": row["machine_name"],
        "platform": row["platform"],
        "version": row["version"],
        "status": row["status"],
        "pairedAt": row["paired_at"],
        "lastSeenAt": row["last_seen_at"],
        "revokedAt": row["revoked_at"],
    }


async def listing(store: Store) -> list[dict[str, Any]]:
    rows = await store.fetch_all("SELECT * FROM installations ORDER BY paired_at DESC")
    return [entry(row) for row in rows]


async def revoke(store: Store, installation_id: str) -> dict[str, Any] | None:
    """Token'ı iptal eder. KAYIT SİLİNMEZ: hangi makinenin ne zaman eşlendiği
    ve ne zaman iptal edildiği denetimin parçasıdır."""
    row = await store.fetch_one("SELECT * FROM installations WHERE id = ?", (installation_id,))
    if row is None:
        return None
    await store.execute(
        "UPDATE installations SET status = 'revoked', revoked_at = ? WHERE id = ?",
        (_now(), installation_id),
    )
    # İptal edilen kurulumun tuttuğu danışma kilitleri de bırakılır; aksi hâlde
    # kayıt TTL dolana dek boş yere uyarı verirdi.
    await store.execute(
        "DELETE FROM advisory_locks WHERE installation_id = ?", (installation_id,)
    )
    fresh = await store.fetch_one("SELECT * FROM installations WHERE id = ?", (installation_id,))
    return entry(fresh) if fresh else None
