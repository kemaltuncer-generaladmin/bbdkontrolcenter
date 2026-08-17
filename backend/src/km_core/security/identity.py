"""Kimlik ve yetki (ADR 0007, docs/identity-model.md).

Kimlik ÇEKİRDEĞE aittir; modül değildir, kapatılamaz.

Giriş kullanıcı adsızdır: **PIN** hem kimliği hem girişi belirler.

  · En az 6 hane, yalnızca rakam. Basit PIN'ler (`BASIT_PINLER`, tek rakamın
    tekrarı, ardışık artan/azalan diziler) reddedilir.
  · **Benzersizdir.** Aynı PIN ikinci kullanıcıya atanamaz; sır kimliği
    belirliyorsa iki kişiye ait olamaz. Çakışma hatası KİMİNLE çakıştığını
    SÖYLEMEZ — aksi hâli, yöneticinin deneme yoluyla başkasının PIN'ini
    öğrenmesine kapı açardı.
  · Argon2id ile hash'lenir; düz PIN hiçbir yerde saklanmaz.
  · Ayrıca sabit anahtarlı (pepper) `secret_lookup` üretilir — girişte tüm
    kullanıcıları tarayıp tek tek Argon2 denemek yerine tek satır bulunur.
    Pepper **kasada** durur, veritabanında değil.
  · Kullanıcı bulunamasa bile doğrulama süresi harcanır: yanıt süresinden
    "bu PIN kimseye ait değil" bilgisi sızmaz. Deneme sayacı
    (`failed_attempts`) ve kilitlenme (`locked_until`) sözleşmenin zorunlu
    parçasıdır.

**ADI ŞİFRE, KURALI PIN.** ADR 0016 (kişiye özel şifreyle giriş) 2026-08-17'de
reddedildi; ancak göçü çoktan koşmuştu. `password_hash`, `secret_lookup`,
`password_set_at` sütunları ve `users.set_password` izin anahtarı ADI DEĞİŞMEDEN
kaldı: adları geri çevirmek ikinci bir göç ve veri riski demekti. Bu dosyada
"password" geçen her ad o sütunlara/anahtara bağlıdır; doğrulama kuralı her
yerde 6 haneli PIN'dir (`validate_pin`).

**ZORLAMA AKIŞI KALDIRILDI (17.08.2026).** 0016'nın "mevcut kullanıcı ilk
girişte sır belirlemeye zorlanır" adımı reddedilmiş kararın göç yoluydu ve geri
alma sırasında kodda unutulmuştu. Gerçek bir kurulumda kullanıcı orijinal
PIN'iyle girdi, akış devreye girip YENİ bir sır yazdı, `secret_lookup` değişti ve
o günden sonra orijinal PIN "bilinmeyen sır" ile reddedildi — yani akış tam da
önlemeyi vaat ettiği şeyi, kilitlenmeyi yaptı. Kalıntı bu yüzden tümüyle
silindi: giriş artık TEK sırra bakar (`secret_lookup`) ve hiçbir koşulda yeni
sır belirlemeye zorlamaz. Geride kalmış satırları `0006_backfill_secret_lookup`
göçü onarır.

`pin_hash` / `pin_lookup` / `pin_set_at` DÜŞÜRÜLMEZ ve hiçbir satır silinmez;
yalnız giriş yolunda okunmazlar.

Kullanıcının kendi PIN'ini İSTEYEREK değiştirmesi (mevcut PIN'ini girerek)
sürer: `set_password_with_secret`. Kaldırılan yalnız ZORLAMAYDI.

K10 — rol adı sorulmaz. Dışarıya açılan tek yetki sorusu `has_permission`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from km_core.store.base import StoreLike

log = structlog.get_logger("km.security")

_hasher = PasswordHasher()

# Kullanıcı bulunamadığında da doğrulama maliyeti ödensin diye kullanılan
# sahte hash. Zamanlama farkını kapatır.
_DUMMY_HASH = _hasher.hash("000000-bulunamadi")

# Çakışma hatası tek tip metindir: hangi kullanıcıyla çakıştığı SÖYLENMEZ.
PASSWORD_UNAVAILABLE = "Bu PIN kullanılamaz, başka bir tane seçin."

# Elenen bilinen PIN'ler. Tek rakam tekrarı ve ardışık diziler ayrıca
# `validate_pin` içinde kuralla elenir; bu küme yalnız kalıplaşmış tercihleri
# kapatır.
BASIT_PINLER = {"123456", "000000", "111111", "654321", "123123", "112233"}

# Ön tanımlı roller (docs/permissions.md). İzinler modüllerin manifestlerinden
# gelir; burada yalnız rollerin VARLIĞI garanti edilir.
BUILTIN_ROLES = [
    ("admin", "Admin", "Tam yetki."),
    ("bld_staff", "BLD Personeli", "BLD sunucuları ve veritabanı."),
    ("bbd_staff", "BBD Personeli", "BBD sunucuları ve veritabanı."),
    ("org_staff", "Kurum Personeli", "Zil, baskı, rehber."),
    ("accountant", "Mali Müşavir", "Mali ekranlar: fatura, cari, vergilendirme, raporlar."),
]

# "Son yönetici" kısıtının dayandığı rol (docs/identity-model.md — Kısıtlar).
#
# BU K10'A AYKIRI DEĞİLDİR. K10, YETKİ sorusunun rol adıyla sorulmasını
# yasaklar (`if role == "admin"` yerine `has_permission`). Buradaki rol bir
# yetki dalı değil, KORUNAN VERİDİR: sistemde yönetici rolü taşıyan son aktif
# kullanıcının pasifleştirilmesini veya rolünün alınmasını engelleyen bir
# bütünlük kısıtıdır. Tek yerde durur ve yetkilendirmede kullanılmaz.
ADMIN_ROLE_ID = "admin"

# `update_user` ile düzenlenebilen alanlar. Sır ve sayaç alanları BURADA
# BULUNMAZ: PIN yalnız `set_password` yolundan, sayaçlar yalnız giriş
# akışından değişir.
EDITABLE_FIELDS = (
    "first_name",
    "last_name",
    "title",
    "department",
    "org_scope",
    "phone_mobile",
    "phone_ext",
    "email",
    "note",
    "directory_visible",
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class CurrentUser:
    id: str
    first_name: str
    last_name: str
    org_scope: str
    roles: list[str]
    permissions: set[str] = field(default_factory=set)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def has_permission(self, key: str, scope: str | None = None) -> bool:
        """Yetki denetiminin TEK yolu. Rol adı sorulmaz (K10)."""
        if key in self.permissions:
            return scope is None
        if f"{key}:*" in self.permissions:
            return True
        return scope is not None and f"{key}:{scope}" in self.permissions


@dataclass(slots=True)
class LoginResult:
    """Giriş sonucu.

    TEK SONUÇ VARDIR: doğrulama geçtiyse oturum açılır ve `token` dolar.
    "Doğrulandı ama oturum açılmadı" diye bir ara durum YOKTUR — o durum
    reddedilmiş ADR 0016'nın zorlama akışıydı ve kaldırıldı (modül başlığı).
    """

    user: CurrentUser
    token: str


class IdentityError(ValueError):
    """Kimlik sözleşmesi ihlali (rol, durum, kısıt)."""


class PasswordError(IdentityError):
    """PIN sözleşmeye uymuyor.

    Sınıf adı göçten kalmıştır (bkz. modül başlığı — "adı şifre, kuralı PIN");
    taşıdığı ihlaller `validate_pin` kurallarıdır.
    """


class RevisionConflict(IdentityError):
    """İyimser kilit çakışması (ADR 0020) — kayıt aradan değişmiş."""


class UserNotFound(IdentityError):
    """Kayıt yok. Kendi sınıfı vardır: `LookupError` kullanılsaydı gövdedeki
    her `KeyError` de sessizce 404'e dönerdi."""


class Identity:
    def __init__(self, store: StoreLike, pepper: str, *, pin_min_length: int = 6,
                 max_failed_attempts: int = 5, lockout_minutes: int = 15,
                 session_idle_minutes: int = 30) -> None:
        self.store = store
        self._pepper = pepper.encode("utf-8")
        self.pin_min_length = pin_min_length
        self.max_failed_attempts = max_failed_attempts
        self.lockout_minutes = lockout_minutes
        self.session_idle_minutes = session_idle_minutes

    # ------------------------------------------------------------------ sır

    def secret_lookup(self, secret: str) -> str:
        """Sabit anahtarlı (pepper) arama hash'i.

        Mekanizma eski `pin_lookup` ile aynıdır; değişen yalnız adıdır — sütun
        adı göçten kalmıştır. Pepper kasadan gelir, veritabanında durmaz (K8).
        """
        return hmac.new(self._pepper, secret.encode("utf-8"), hashlib.sha256).hexdigest()

    def adopt_pepper(self, pepper: str) -> None:
        """Çalışan nesnenin pepper'ını değiştirir — YALNIZ cihaz eşlemesi için.

        Pepper açılışta kasadan okunup belleğe alınıyor. Kurulum merkeze
        eşlenip merkezin anahtarını benimsediğinde bu nesne hâlâ ESKİ değeri
        taşır ve yeniden başlatılana kadar hiçbir PIN tutmaz — kullanıcı eşleme
        ekranından çıkar ve yine giremez.

        BAŞKA HİÇBİR YERDEN ÇAĞRILMAZ. Pepper "bir kez belirlenir, bir daha
        değişmez" (`services/identity/README.md`); buradaki tek istisna, henüz
        kimseyi bağlamamış taze bir kurulumun merkeze katılma anıdır ve o kararı
        `IdentitySync._adopt_pepper` veriyor.
        """
        self._pepper = pepper.encode("utf-8")

    def validate_pin(self, pin: str) -> None:
        """PIN kuralları (ADR 0007, docs/identity-model.md).

        Sözleşmenin tamamı burada: yalnız rakam, en az `pin_min_length` hane,
        kalıplaşmış ve ardışık diziler kapalı. Uzunluk sınırı yukarıdan
        `config/default.yaml` → `auth.pin_min_length` ile gelir.
        """
        if not pin.isdigit():
            raise PasswordError("PIN yalnızca rakamlardan oluşur.")
        if len(pin) < self.pin_min_length:
            raise PasswordError(f"PIN en az {self.pin_min_length} hane olmalı.")
        if pin in BASIT_PINLER or len(set(pin)) == 1:
            raise PasswordError("Bu PIN fazla basit.")
        # ardışık artan/azalan dizi
        digits = [int(char) for char in pin]
        if all(b - a == 1 for a, b in pairwise(digits)):
            raise PasswordError("Ardışık PIN kullanılamaz.")
        if all(a - b == 1 for a, b in pairwise(digits)):
            raise PasswordError("Ardışık PIN kullanılamaz.")

    async def _assert_password_free(self, lookup: str, *, except_user_id: str | None = None) -> None:
        """Benzersizlik kapısı.

        Hata KİMİNLE çakıştığını söylemez; aksi hâli yöneticinin deneme yoluyla
        başkasının PIN'ini öğrenmesine kapı açardı.
        """
        row = await self.store.fetch_one(
            "SELECT id FROM users WHERE secret_lookup = ? AND id IS NOT ?",
            (lookup, except_user_id),
        )
        if row is not None:
            raise PasswordError(PASSWORD_UNAVAILABLE)

    # --------------------------------------------------------------- roller

    async def ensure_builtin_roles(self) -> None:
        for role_id, name, description in BUILTIN_ROLES:
            await self.store.execute(
                "INSERT INTO roles (id, name, description, builtin) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                (role_id, name, description),
            )

    async def grant_defaults(self, permissions: list[dict[str, Any]]) -> None:
        """Modül manifestlerindeki `default_roles` önerilerini uygular.

        Yalnızca EKLER; yöneticinin sonradan kaldırdığı izni geri getirmemek
        için mevcut satıra dokunmaz.
        """
        rows = []
        for permission in permissions:
            key = permission["key"]
            entry = f"{key}:*" if permission.get("scoped") else key
            for role_id in permission.get("default_roles") or []:
                rows.append((role_id, entry))
        if rows:
            await self.store.execute_many(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
                rows,
            )

    async def list_roles(self) -> list[dict[str, Any]]:
        roles = await self.store.fetch_all(
            "SELECT id, name, description, builtin FROM roles ORDER BY id"
        )
        grants = await self.store.fetch_all(
            "SELECT role_id, permission FROM role_permissions ORDER BY role_id, permission"
        )
        by_role: dict[str, list[str]] = {}
        for grant in grants:
            by_role.setdefault(str(grant["role_id"]), []).append(str(grant["permission"]))
        return [
            {
                "id": role["id"],
                "name": role["name"],
                "description": role["description"],
                "builtin": bool(role["builtin"]),
                "permissions": by_role.get(str(role["id"]), []),
            }
            for role in roles
        ]

    async def _assert_roles_exist(self, roles: list[str]) -> None:
        if not roles:
            raise IdentityError("En az bir rol zorunlu.")
        known = {str(row["id"]) for row in await self.store.fetch_all("SELECT id FROM roles")}
        unknown = [role for role in roles if role not in known]
        if unknown:
            raise IdentityError(f"Tanımsız rol: {', '.join(sorted(unknown))}")

    # ----------------------------------------------------------- kullanıcı

    async def create_user(self, *, first_name: str, last_name: str, org_scope: str,
                          password: str, roles: list[str], created_by: str | None = None,
                          **extra: Any) -> str:
        phone_mobile = extra.get("phone_mobile")
        # Argüman adı `password`, denetlenen şey PIN'dir (modül başlığı).
        self.validate_pin(password)
        await self._assert_roles_exist(roles)

        lookup = self.secret_lookup(password)
        await self._assert_password_free(lookup)

        user_id = str(uuid.uuid4())
        # Eski PIN sütunları DÜŞÜRÜLMEDİ ve `NOT NULL`. Yeni kullanıcının PIN'i
        # `password_hash` sütununda durur; eski sütunlar giriş yolunda asla
        # eşleşmeyecek bir yer tutucuyla doldurulur (`pin_lookup` HMAC hexdigest
        # biçiminde olmadığı için hiçbir sırla çakışamaz).
        await self.store.execute(
            "INSERT INTO users (id, first_name, last_name, title, department, org_scope, "
            "phone_mobile, phone_ext, email, note, directory_visible, status, pin_hash, "
            "pin_lookup, pin_set_at, password_hash, secret_lookup, password_set_at, "
            "created_at, updated_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, first_name, last_name, extra.get("title"), extra.get("department"),
                org_scope, phone_mobile, extra.get("phone_ext"), extra.get("email"),
                extra.get("note"), int(extra.get("directory_visible", True)), "active",
                "", f"pin-yok:{user_id}", now(),
                _hasher.hash(password), lookup, now(),
                now(), now(), created_by,
            ),
        )
        await self.store.execute_many(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
            [(user_id, role) for role in roles],
        )
        await self.audit(created_by, "users.create", result="ok", detail=user_id)
        return user_id

    async def user_count(self) -> int:
        row = await self.store.fetch_one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    async def list_users(self) -> list[dict[str, Any]]:
        rows = await self.store.fetch_all(
            "SELECT * FROM users ORDER BY first_name, last_name"
        )
        grants = await self.store.fetch_all(
            "SELECT user_id, role_id FROM user_roles ORDER BY user_id, role_id"
        )
        by_user: dict[str, list[str]] = {}
        for grant in grants:
            by_user.setdefault(str(grant["user_id"]), []).append(str(grant["role_id"]))
        return [{**row, "roles": by_user.get(str(row["id"]), [])} for row in rows]

    async def get_user(self, user_id: str) -> dict[str, Any]:
        row = await self.store.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            raise UserNotFound("Kullanıcı bulunamadı.")
        return {**row, "roles": await self.roles_of(user_id)}

    async def permissions_of(self, user_id: str) -> set[str]:
        rows = await self.store.fetch_all(
            "SELECT rp.permission FROM role_permissions rp "
            "JOIN user_roles ur ON ur.role_id = rp.role_id WHERE ur.user_id = ?",
            (user_id,),
        )
        return {row["permission"] for row in rows}

    async def roles_of(self, user_id: str) -> list[str]:
        rows = await self.store.fetch_all(
            "SELECT role_id FROM user_roles WHERE user_id = ? ORDER BY role_id", (user_id,)
        )
        return [row["role_id"] for row in rows]

    async def update_user(self, user_id: str, *, fields: dict[str, Any],
                          roles: list[str] | None = None,
                          expected_revision: int | None = None,
                          actor_id: str | None = None) -> dict[str, Any]:
        """Kullanıcı kaydını günceller. İyimser kilit ZORUNLU değil, ama
        `expected_revision` gönderilmişse BAĞLAYICIDIR (ADR 0020).

        Sessizce korunuyormuş gibi davranmak hiç korumamaktan kötüdür: beklenen
        revizyon uymuyorsa yazma yapılmaz, 409 doğar.
        """
        row = await self.get_user(user_id)
        current = int(row.get("revision") or 1)
        if expected_revision is not None and expected_revision != current:
            raise RevisionConflict(
                f"Bu kayıt aradan değişti (son değişiklik: {row['updated_at']}). "
                "Yenileyip tekrar deneyin."
            )

        unknown = [name for name in fields if name not in EDITABLE_FIELDS]
        if unknown:
            raise IdentityError(f"Düzenlenemeyen alan: {', '.join(sorted(unknown))}")

        if roles is not None:
            await self._assert_roles_exist(roles)
            await self._assert_admin_remains(user_id, keeps_admin=ADMIN_ROLE_ID in roles)

        assignments = [f"{name} = ?" for name in fields]
        values: list[Any] = [
            int(bool(value)) if name == "directory_visible" else value
            for name, value in fields.items()
        ]
        assignments += ["updated_at = ?", "revision = revision + 1"]
        values += [now(), user_id]
        await self.store.execute(
            f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", values
        )

        if roles is not None:
            await self.store.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            await self.store.execute_many(
                "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                [(user_id, role) for role in roles],
            )
            await self.audit(actor_id, "roles.manage", result="ok",
                             detail=f"{user_id}: {', '.join(sorted(roles))}")

        await self.audit(actor_id, "users.update", result="ok", detail=user_id)
        return await self.get_user(user_id)

    async def set_status(self, user_id: str, status: str, *,
                         actor_id: str | None = None) -> dict[str, Any]:
        if status not in ("active", "disabled"):
            raise IdentityError("Durum yalnız 'active' veya 'disabled' olabilir.")
        await self.get_user(user_id)
        await self._assert_admin_remains(user_id, keeps_admin=status == "active")
        await self.store.execute(
            "UPDATE users SET status = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
            (status, now(), user_id),
        )
        if status != "active":
            # Pasifleşen kullanıcının açık oturumları da kapanır.
            await self.store.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await self.audit(actor_id, "users.status", result="ok", detail=f"{user_id}: {status}")
        return await self.get_user(user_id)

    async def _assert_admin_remains(self, user_id: str, *, keeps_admin: bool) -> None:
        """Son yönetici pasifleştirilemez, rolü alınamaz (identity-model.md).

        Bütünlük kısıtıdır, yetkilendirme değildir — bkz. `ADMIN_ROLE_ID` notu.
        """
        if keeps_admin:
            return
        rows = await self.store.fetch_all(
            "SELECT ur.user_id FROM user_roles ur JOIN users u ON u.id = ur.user_id "
            "WHERE ur.role_id = ? AND u.status = 'active'",
            (ADMIN_ROLE_ID,),
        )
        admins = {str(row["user_id"]) for row in rows}
        if user_id in admins and len(admins) <= 1:
            raise IdentityError(
                "Sistemde yönetici rolü taşıyan son aktif kullanıcı bu; "
                "pasifleştirilemez ve rolü alınamaz."
            )

    # ---------------------------------------------------------------- PIN

    async def set_password(self, user_id: str, password: str, *,
                           actor_id: str | None = None) -> None:
        """PIN atar/sıfırlar. Düz PIN hiçbir yerde saklanmaz, denetim izine de
        yazılmaz.

        Metot adı ve denetim eylemi (`users.set_password`) izin anahtarıyla aynı
        kalır — anahtar göçle yazıldı, geri çevirmek ikinci bir göç demekti.
        """
        await self.get_user(user_id)
        self.validate_pin(password)
        lookup = self.secret_lookup(password)
        await self._assert_password_free(lookup, except_user_id=user_id)

        await self.store.execute(
            "UPDATE users SET password_hash = ?, secret_lookup = ?, password_set_at = ?, "
            "failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
            (_hasher.hash(password), lookup, now(), now(), user_id),
        )
        await self.audit(actor_id, "users.set_password", result="ok", detail=user_id)

    async def set_password_with_secret(
        self, current_secret: str, password: str
    ) -> tuple[str, CurrentUser] | None:
        """Kullanıcının kendi PIN'ini İSTEYEREK değiştirmesi.

        `current_secret` mevcut PIN'dir. Doğrulanamazsa None döner — sebep AYIRT
        EDİLMEZ. Başarıda oturum açılır: PIN'ini değiştiren kullanıcı ikinci kez
        giriş yapmak zorunda kalmaz.

        BU YOL KİMSEYİ ZORLAMAZ. Giriş akışı buraya asla yönlendirmez; çağrı
        yalnızca kullanıcının kendi isteğiyle başlar.
        """
        row = await self._find_by_secret(current_secret)
        if row is None:
            self._burn_time(current_secret)
            await self.audit(None, "auth.set_password", result="fail", detail="bilinmeyen sır")
            return None

        if self._locked(row):
            await self.audit(row["id"], "auth.set_password", result="fail", detail="kilitli")
            return None

        if not _verify(row["password_hash"], current_secret):
            await self._register_failure(row, action="auth.set_password")
            return None

        if row["status"] != "active":
            await self.audit(row["id"], "auth.set_password", result="fail",
                             detail="pasif kullanıcı")
            return None

        await self.set_password(str(row["id"]), password, actor_id=str(row["id"]))
        fresh = await self.get_user(str(row["id"]))
        return await self._open_session(fresh)

    # ------------------------------------------------------------- giriş

    async def login(self, password: str) -> LoginResult | None:
        """PIN ile giriş. Başarısızsa None — sebep AYIRT EDİLMEZ.

        Argüman adı uçtaki alan adıyla (`password`) aynı kalır; beklenen değer
        6 haneli PIN'dir.

        DOĞRULAMA GEÇTİYSE OTURUM AÇILIR. Bu metot hiçbir koşulda kullanıcıyı
        yeni sır belirlemeye zorlamaz (modül başlığı).
        """
        row = await self._find_by_secret(password)

        if row is None:
            # Sahte doğrulama: yanıt süresi kullanıcı varmış gibi geçsin.
            self._burn_time(password)
            await self.audit(None, "auth.login", result="fail", detail="bilinmeyen sır")
            return None

        if self._locked(row):
            await self.audit(row["id"], "auth.login", result="fail", detail="kilitli")
            return None

        if not _verify(row["password_hash"], password):
            await self._register_failure(row, action="auth.login")
            return None

        if row["status"] != "active":
            await self.audit(row["id"], "auth.login", result="fail", detail="pasif kullanıcı")
            return None

        await self.store.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (row["id"],),
        )

        token, user = await self._open_session(row)
        return LoginResult(user=user, token=token)

    async def _find_by_secret(self, secret: str) -> dict[str, Any] | None:
        """Arama TEK HMAC ile TEK sütundan tek satıra iner.

        İKİNCİ BİR YOL YOKTUR. Eskiden `secret_lookup` tutmazsa
        `pin_lookup AND password_hash IS NULL` da denenirdi; o yol reddedilmiş
        ADR 0016'nın göç yoluydu ve iki kusuru vardı: `password_hash` dolduğu an
        sessizce devre dışı kalıyordu (kullanıcı nedenini göremiyordu) ve
        bağlandığı zorlama akışı gerçek bir kurulumu kilitledi. Geride kalmış
        satırlar artık girişte değil, GÖÇTE onarılır
        (`migrations.py` → `0006_backfill_secret_lookup`).
        """
        return await self.store.fetch_one(
            "SELECT * FROM users WHERE secret_lookup = ?", (self.secret_lookup(secret),)
        )

    def _locked(self, row: dict[str, Any]) -> bool:
        locked_until = row.get("locked_until")
        if not locked_until:
            return False
        return datetime.fromisoformat(str(locked_until)) > datetime.now(UTC)

    def _burn_time(self, secret: str) -> None:
        """Kullanıcı yokken de Argon2 maliyeti ödenir — zamanlama sızıntısı
        kapalı kalır."""
        _verify(_DUMMY_HASH, secret)

    async def _open_session(self, row: dict[str, Any]) -> tuple[str, CurrentUser]:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(minutes=self.session_idle_minutes)
        await self.store.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_seen, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash_token(token), row["id"], now(), now(), expires.isoformat(timespec="seconds")),
        )
        await self.store.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?", (now(), row["id"])
        )
        await self.audit(row["id"], "auth.login", result="ok")
        return token, await self._current_user(row)

    async def _register_failure(self, row: dict[str, Any], *, action: str) -> None:
        attempts = int(row["failed_attempts"]) + 1
        locked = None
        if attempts >= self.max_failed_attempts:
            locked = (datetime.now(UTC) + timedelta(minutes=self.lockout_minutes)).isoformat(
                timespec="seconds"
            )
        await self.store.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (attempts, locked, row["id"]),
        )
        await self.audit(
            row["id"], action, result="fail",
            detail=f"hatalı PIN ({attempts}){' — kilitlendi' if locked else ''}",
        )

    async def resolve_session(self, token: str) -> CurrentUser | None:
        row = await self.store.fetch_one(
            "SELECT s.user_id, s.expires_at, u.* FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (_hash_token(token),),
        )
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
            await self.store.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))
            return None
        if row["status"] != "active":
            return None

        # Boşta kalma süresi her istekte tazelenir.
        expires = datetime.now(UTC) + timedelta(minutes=self.session_idle_minutes)
        await self.store.execute(
            "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token = ?",
            (now(), expires.isoformat(timespec="seconds"), _hash_token(token)),
        )
        return await self._current_user(row)

    async def logout(self, token: str) -> None:
        await self.store.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))

    async def _current_user(self, row: dict[str, Any]) -> CurrentUser:
        user_id = row["id"]
        return CurrentUser(
            id=user_id,
            first_name=row["first_name"],
            last_name=row["last_name"],
            org_scope=row["org_scope"],
            roles=await self.roles_of(user_id),
            permissions=await self.permissions_of(user_id),
        )

    # ------------------------------------------------------------- denetim

    async def audit(self, user_id: str | None, action: str, *, result: str,
                    scope: str | None = None, detail: str | None = None) -> None:
        """Denetim izi. PIN VE SIR DEĞERLERİ BURAYA YAZILMAZ."""
        await self.store.execute(
            "INSERT INTO audit_log (at, user_id, action, scope, result, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now(), user_id, action, scope, result, detail),
        )


def _verify(stored: str | None, secret: str) -> bool:
    """Argon2 doğrulaması. Bozuk/boş hash de sessizce `False` döner (K7)."""
    try:
        return bool(_hasher.verify(stored or "", secret))
    except (VerificationError, InvalidHashError):
        return False


def _hash_token(token: str) -> str:
    """Oturum belirteci veritabanında düz durmaz."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
