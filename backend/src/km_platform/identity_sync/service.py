"""Kimlik senkronu yeteneği (ADR 0021 — Kontrol Merkezi tarafı).

Sözleşme:

    await sync.state()                  → ekranın ihtiyacı olan her şey
    await sync.pair(code)               → eşleme; token kasaya yazılır
    await sync.unpair()                 → eşlemeyi çözer; özel anahtar KALIR
    await sync.sync()                   → revision değişmişse kadroyu çeker
    sync.login_policy()                 → "local" | "online_only"
    await sync.create_pair_code(note)   → yeni kurulumlar için tek kullanımlık kod
    await sync.installations()          → merkezdeki kurulum listesi
    await sync.revoke_installation(id)  → kurulumu iptal eder; satır SİLİNMEZ
    await sync.create_user(actor, body) → yalnız çevrimiçi; yoksa DENEMEZ
    await sync.push_audit(entries)      → kuyruğa yazar, sonra göndermeyi dener
    await sync.flush_audit()            → kuyruğu boşaltmayı dener (hata yükseltmez)

Çekilen kadronun yerel `users`/`roles`/`user_roles` tablolarına YANSITILMASI bu
nesnenin işi değildir: yansıtma çekirdektedir
(`km_core/security/roster_projection.py`) ve giriş yolundan çağrılır. Bu nesne
yalnız veriyi getirip önbelleğe yazar.

**KAPALIYKEN HİÇBİR ŞEY DEĞİŞMEZ.** `platform.identity_sync.enabled` kapalı
olduğunda bu nesne yine kayıtlıdır ama hiçbir ağ isteği yapmaz ve
`login_policy()` her zaman `"local"` döner — Kontrol Merkezi bugünkü gibi tek
makinede çalışır (ADR 0021 — Sonuçlar).
"""

from __future__ import annotations

import hashlib
import platform as platform_info
import socket
from time import monotonic
from typing import Any, Protocol

import structlog

from km_core.config.loader import Config

from .cache import RosterCache
from .client import CLIENT_VERSION, IdentityClient
from .errors import (
    IdentitySyncError,
    ManagementKeyMissing,
    NotPaired,
    WriteRequiresConnection,
)
from .queue import BATCH_SIZE, AuditQueue, QueueStore

log = structlog.get_logger("km.identity_sync")

# Kasa anahtarları. `core.pin_pepper` ile aynı adlandırma: <alan>.<ad>.
TOKEN_KEY = "identity_sync.installation_token"
PRIVATE_KEY = "identity_sync.private_key"
INSTALLATION_ID_KEY = "identity_sync.installation_id"
# Merkezin yönetim anahtarı (`KM_IDENTITY_ADMIN_TOKEN` karşılığı). Kurulum
# token'ından AYRIDIR: biri "bu makine bizim", öteki "yeni makine kaydedebilir"
# der. Ayara yazılmaz, kasada durur (K8).
ADMIN_TOKEN_KEY = "identity_sync.admin_token"

# Çevrimiçilik bilgisinin tazelik süresi. Her yazmadan önce `/health` sormak
# gereksiz; hiç sormamak ise "ağ yoksa denemez" kuralını boşa çıkarır.
ONLINE_PROBE_TTL_SECONDS = 15.0


def pepper_fingerprint(pepper: str) -> str:
    """Merkezdeki `pepper_fingerprint` ile AYNI hesap — iki taraf da sha256'nın
    ilk 16 hex hanesini kullanır. Anahtarın kendisi taşınmaz."""
    return hashlib.sha256(pepper.encode("utf-8")).hexdigest()[:16]


class SecretStore(Protocol):
    """Kasanın yeteneğe bakan yüzü (`km_platform/secrets/vault.py`)."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...
    async def delete(self, key: str) -> None: ...

    # Cihaz eşlemesi merkezin kimlik anahtarını benimsemek zorunda; bu ikisi o
    # kararı verebilmek için gerekli (bkz. `_adopt_pepper`).
    async def pepper_is_auto(self) -> bool: ...
    async def adopt_pepper(self, value: str) -> None: ...

    #: Kasanın "kendiliğinden üretildi" işareti — sıfırlama bunu geri koyar.
    AUTO_FLAG: str


class IdentitySync:
    def __init__(self, vault: SecretStore, config: Config,
                 client: IdentityClient | None = None,
                 store: QueueStore | None = None) -> None:
        self._vault = vault
        settings = config.section("platform").get("identity_sync") or {}
        self.enabled = bool(settings.get("enabled", False))
        self.base_url = str(settings.get("base_url") or "").strip()
        self.timeout_seconds = float(settings.get("timeout_seconds", 10))
        self.max_cache_age_hours = float(settings.get("max_cache_age_hours", 72))
        self.require_pairing = bool(settings.get("require_pairing", True))
        self.cache = RosterCache(
            config.path("platform.identity_sync.cache_path", "data/identity-roster.json")
        )
        self._client = client or IdentityClient(
            self.base_url, timeout_seconds=self.timeout_seconds
        )
        # `None` = HENÜZ BİLİNMİYOR. `False` ile karıştırılmaz: bilmediğimiz bir
        # şeyi "ağ yok" saymak, çalışan bir merkezde yazmayı reddetmek olurdu.
        #: Merkezin kimlik anahtarıyla bu kurulumunki ayrıştı mı (bkz. `_check_pepper`).
        self.pepper_mismatch = False
        self._online: bool | None = None
        self._online_checked_at: float = 0.0
        self._store: QueueStore | None = store
        self._queue: AuditQueue | None = AuditQueue(store) if store is not None else None

    def attach_store(self, store: QueueStore) -> None:
        """Denetim kuyruğunun deposunu bağlar (ADR 0021 §5).

        Yetenek `Vault` ve `Config` ile kurulur; çekirdek deposu kurucuya
        verilmez. Bağlama bu yüzden ilk istekte, kuyruk gerçekten gerekli
        olduğunda yapılır — `km_platform/identity_sync/http.py` ve giriş yolu
        çağırır. **İkinci çağrı hiçbir şey yapmaz**: bağlanmış bir kuyruğu
        değiştirmek, içindeki bekleyen kayıtları görünmez kılardı.

        Depo ayrıca `unpair()` için tutulur: eşleme çözülünce merkezden
        yansıtılmış kullanıcıların pasifleştirilmesi gerekiyor ve o satırlar
        çekirdeğin `users` tablosundadır.
        """
        if self._queue is None:
            self._store = store
            self._queue = AuditQueue(store)

    # ------------------------------------------------------------- durum

    @property
    def configured(self) -> bool:
        """Yetenek gerçekten kullanılabilir mi? Adres verilmemiş bir "açık"
        ayar, açık değildir."""
        return self.enabled and bool(self.base_url)

    async def installation_token(self) -> str | None:
        if not self.configured:
            return None
        return await self._vault.get(TOKEN_KEY)

    async def installation_id(self) -> str | None:
        """Merkezin bu makineye verdiği kimlik. Ekranda görünür — sır değildir,
        kurulum listesindeki satırı işaret eder."""
        if not self.configured:
            return None
        return await self._vault.get(INSTALLATION_ID_KEY)

    async def is_paired(self) -> bool:
        return bool(await self.installation_token())

    async def pairing_required(self) -> bool:
        """Kabuk giriş ekranı yerine EŞLEME ekranı mı açmalı?

        Yetenek kapalıysa `False` — eşleme ekranı, merkezi olmayan bir kurulumu
        hiç açılamaz hâle getirirdi.
        """
        if not self.configured or not self.require_pairing:
            return False
        return not await self.is_paired()

    def login_policy(self) -> str:
        """`"local"` = çevrimdışı giriş kabul edilir · `"online_only"` = edilmez.

        **GİRİŞ ÇEVRİMDIŞI ÇALIŞIR** (ADR 0021 §2) — varsayılan budur. Tek
        istisna, önbelleğin ayardaki yaş sınırını aşmasıdır: pasifleştirilen bir
        kullanıcının çevrimdışı bir makinede sonsuza dek giriş yapabilmesi kabul
        edilemez.
        """
        if not self.configured:
            return "local"
        if self.cache.is_stale(self.max_cache_age_hours):
            return "online_only"
        return "local"

    async def state(self) -> dict[str, Any]:
        """Eşleme ekranının ve sağlık ekranının tek kaynağı.

        SIR DÖNMEZ: token da, kadro alanları da yok; yalnız durum.
        """
        paired = await self.is_paired()
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "baseUrl": self.base_url,
            "paired": paired,
            "installationId": await self.installation_id(),
            "pairingRequired": await self.pairing_required(),
            "loginPolicy": self.login_policy(),
            "maxCacheAgeHours": self.max_cache_age_hours,
            "cache": self.cache.summary(),
            "machine": self.machine(),
            "online": self._online,
            # Yönetim anahtarının VARLIĞI bildirilir, KENDİSİ değil. Ekran
            # "kurulum listesi çekilebilir mi" sorusunu buradan yanıtlar ve
            # olmayan bir yetenek için düğme çizmez.
            "managementKey": bool(await self._vault.get(ADMIN_TOKEN_KEY)),
            # True ise bu kurulumda HİÇBİR PIN çalışmaz; ekran bunu söyleyip
            # yeniden eşlemeyi önerir.
            "pepperMismatch": self.pepper_mismatch
            if self.configured else False,
            # Kuyrukta bekleyen denetim kaydı. "Asla düşürülmez" sözünün
            # görünür karşılığı budur: sayı büyüyorsa merkez ulaşılamıyordur.
            "auditPending": await self.audit_pending(),
        }

    def machine(self) -> dict[str, str]:
        """Merkezdeki "Kurulumlar" listesinin gördüğü kimlik (ADR 0021 §4.1)."""
        return {
            "machineName": socket.gethostname() or "bilinmeyen",
            "platform": platform_info.system() or "bilinmeyen",
            "version": CLIENT_VERSION,
        }

    # ---------------------------------------------------------- çevrimiçilik

    async def probe(self) -> bool:
        """Merkez ayakta mı? Yazma denemesinden ÖNCE sorulan tek soru budur.

        `/health` sormak yazma denemek değildir: reddedilen bir yazmanın
        "belki gitmiştir" belirsizliği doğmaz (ADR 0021 §3).
        """
        if not self.configured:
            self._online = False
            return False
        try:
            await self._client.health()
        except IdentitySyncError:
            self._online = False
        else:
            self._online = True
        self._online_checked_at = monotonic()
        return self._online

    async def _ensure_online(self) -> bool:
        fresh = (monotonic() - self._online_checked_at) < ONLINE_PROBE_TTL_SECONDS
        if self._online is not None and fresh:
            return self._online
        return await self.probe()

    # ------------------------------------------------------------ eşleme

    async def pair(self, code: str) -> dict[str, Any]:
        """Kodu merkeze verir, kurulum token'ını KASAYA yazar (K8).

        Anahtar çifti burada üretilir ve açık anahtar merkeze gider (ADR 0021
        §4.4). Özel anahtar kasadan hiç çıkmaz.
        """
        if not self.configured:
            raise IdentitySyncError("Kimlik servisi ayarlanmamış.")

        public_key = await self._ensure_key_pair()
        machine = self.machine()
        result = await self._client.pair(
            code=code.strip(),
            public_key=public_key,
            machine_name=machine["machineName"],
            platform=machine["platform"],
            version=machine["version"],
        )
        # PEPPER, KADRODAN ÖNCE BENİMSENİR. Sıra pazarlık konusu değil: kadro
        # `secret_lookup` taşır ve o değer pepper'la üretilir. Önce kadro
        # çekilseydi satırlar yerel (yanlış) pepper'a göre yansıtılır ve hiçbir
        # PIN tutmazdı — belirti de sebebi ele vermezdi.
        pepper = str(result.get("pepper") or "")
        benimsendi = await self._adopt_pepper(pepper)

        await self._vault.set(TOKEN_KEY, str(result["token"]))
        await self._vault.set(INSTALLATION_ID_KEY, str(result["installationId"]))
        self._online = True
        self._online_checked_at = monotonic()
        log.info("kurulum eşlendi", installation=result["installationId"])
        # Eşlemenin hemen ardından kadro çekilir: kullanıcı eşleme ekranından
        # boş bir giriş ekranına düşmemeli.
        await self.sync()
        # `pepper` çağırana DÖNER: çalışan `Identity` nesnesi açılışta eski
        # değeri belleğine almıştı ve yeniden başlatılmadan onu bilmez. HTTP
        # katmanı bunu görüp canlı nesneyi tazeler; yoksa kullanıcı eşleme
        # ekranından çıkıp yine giremezdi.
        return {
            "paired": True,
            "installationId": result["installationId"],
            "pepperAdopted": benimsendi,
            "pepper": pepper if benimsendi else "",
        }

    async def _adopt_pepper(self, pepper: str) -> bool:
        """Merkezin pepper'ını kasaya yazar. Dönüş: gerçekten değişti mi.

        ÜÇ DURUM VE ÜÇÜ DE FARKLI:

          · Merkez pepper göndermedi (eski sürüm) → dokunma. Kadro yine çekilir;
            pepper zaten tutuyorsa çalışır, tutmuyorsa eski davranış sürer.
          · Yereldeki değer merkezinkiyle AYNI → yapacak bir şey yok.
          · Farklı → yalnız yerel değer KENDİLİĞİNDEN doğduysa ezilir. Elle
            konmuş ya da daha önce benimsenmiş bir pepper'ı ezmek, o makinedeki
            herkesin girişini bir anda kırardı ve düz PIN'ler hiçbir yerde
            saklanmadığı için geri getirilemezdi.
        """
        if not pepper:
            log.warning("merkez pepper göndermedi — eski sürüm olabilir")
            return False

        mevcut = await self._vault.get("core.pin_pepper")
        if mevcut == pepper:
            return False

        if mevcut is not None and not await self._vault.pepper_is_auto():
            raise IdentitySyncError(
                "Bu kurulumun kendi kimlik anahtarı var ve merkezinkinden farklı. "
                "Eşleme sürdürülseydi buradaki kullanıcıların hiçbiri giriş "
                "yapamazdı. Önce bu makinenin kadrosunu merkeze taşıyın."
            )

        await self._vault.adopt_pepper(pepper)
        log.warning("merkezin kimlik anahtarı benimsendi — yereldeki değiştirildi")
        return True

    async def _ensure_key_pair(self) -> str:
        """Ed25519 çifti. Varsa kasadan okunur, yoksa üretilir.

        `cryptography` zaten bağımlılıkta (kasa Fernet için kullanıyor).
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        stored = await self._vault.get(PRIVATE_KEY)
        if stored:
            private = serialization.load_pem_private_key(stored.encode("utf-8"), password=None)
        else:
            private = ed25519.Ed25519PrivateKey.generate()
            pem = private.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            await self._vault.set(PRIVATE_KEY, pem.decode("ascii"))

        return private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    # -------------------------------------------------------- kurulum yönetimi
    #
    # Üçü de YAZMA YOLUNDADIR (ADR 0021 §3): ağ yoksa DENENMEZ. Listeleme bir
    # okuma gibi görünse de merkezin kaydını okur; önbelleği yoktur ve
    # olmamalıdır — bayat bir kurulum listesine bakarak "bu makineyi iptal
    # ettim" demek, iptal edilmemiş bir makineyi iptal edilmiş sanmaktır.

    async def _management_token(self) -> str:
        if not self.configured:
            raise IdentitySyncError("Kimlik servisi ayarlanmamış.")
        token = await self._vault.get(ADMIN_TOKEN_KEY)
        if not token:
            raise ManagementKeyMissing
        if not await self._ensure_online():
            raise WriteRequiresConnection
        return token

    async def create_pair_code(self, note: str | None = None) -> dict[str, Any]:
        """Yeni eşleme kodu üretir.

        **YENİ KOD BEKLEYEN ESKİ KODLARI GEÇERSİZ KILAR** — kararı merkez
        uygular (`services/identity/app/installations.py`). Ekranda yazılı olan
        cümle budur ve kodun karşılığı oradadır.

        SÜREYİ MERKEZ SÖYLER. `expiresAt` yanıtta gelir; arayüz yalnız geri
        sayar. Süreyi burada hesaplamak, ayarı değişen bir merkezle sessizce
        yalan söyleyen bir sayaç üretirdi.

        KOD DENETİM İZİNE YAZILMAZ. Kod bir sırdır ve iz satırı silinmez; koda
        yazsaydık kodun ömrü on dakika yerine sonsuz olurdu.
        """
        token = await self._management_token()
        result = await self._client.create_pair_code(token, note=note)
        log.info("eşleme kodu üretildi", expires=result.get("expiresAt"))
        return result

    async def installations(self) -> list[dict[str, Any]]:
        """Merkezdeki kurulum listesi. Token ve açık anahtar merkezde zaten
        listeye girmez."""
        token = await self._management_token()
        return await self._client.installations(token)

    async def revoke_installation(self, installation_id: str) -> dict[str, Any]:
        """Kurulumu iptal eder. SATIR SİLİNMEZ, durumu değişir."""
        token = await self._management_token()
        row = await self._client.revoke_installation(token, installation_id)
        log.warning("kurulum iptal edildi", installation=installation_id)
        return row

    async def unpair(self) -> dict[str, Any]:
        """BU MAKİNENİN eşlemesini çözer. Merkeze gitmez, YERELDE çalışır.

        Merkezdeki satırı düşürmek `revoke_installation()`ın işidir ve ayrı bir
        karardır: ağ yokken de bir makineyi merkezden koparabilmek gerekir
        (çalınan dizüstü), ama merkezdeki kaydı silmek çevrimiçilik ister.

        Ne olur, ne olmaz:

          · `installation_token` ve `installation_id` KASADAN SİLİNİR.
          · `private_key` SİLİNMEZ. Aynı makine yeniden eşlendiğinde merkez onu
            aynı açık anahtarla tanır; anahtarı atmak, her eşlemede yeni bir
            kimlik doğurup merkezdeki geçmişi koparırdı.
          · `origin='central'` kullanıcılar PASİFLEŞTİRİLİR, SİLİNMEZ. Merkezden
            gelen kadro artık tazelenemez; o satırların girişte kabul edilmeye
            devam etmesi, merkezden çıkarılmış birinin bu makinede süresiz
            girebilmesi demekti. Silmek ise denetim izindeki "kim yaptı"
            bağını öksüz bırakırdı.
          · `origin='local'` kullanıcılara DOKUNULMAZ. Bu makinenin kendi
            kadrosu eşlemeden önce de vardı, sonra da durur — yoksa eşlemeyi
            çözen yönetici kendini dışarıda bırakırdı.
          · Kadro önbelleği silinir: içinde merkezin `password_hash` kopyaları
            var ve eşleme çözülmüşken onları tutmanın hiçbir karşılığı yok.
        """
        await self._vault.delete(TOKEN_KEY)
        await self._vault.delete(INSTALLATION_ID_KEY)

        disabled = await self._disable_central_users()

        try:
            self.cache.path.unlink(missing_ok=True)
        except OSError as error:  # pragma: no cover — salt okunur bağlama vb.
            log.warning("kadro önbelleği silinemedi", error=str(error))

        self._online = None
        self._online_checked_at = 0.0
        log.warning("kurulum eşlemesi çözüldü", disabled=disabled)
        return {"paired": False, "disabledUsers": disabled}

    async def _disable_central_users(self) -> int:
        """Merkezden yansıtılmış kullanıcıları pasifleştirir ve oturumlarını
        kapatır. Depo bağlanmamışsa hiçbir şey yapmaz (K7)."""
        if self._store is None:
            return 0
        rows = await self._store.fetch_all(
            "SELECT id FROM users WHERE origin = 'central' AND status = 'active'"
        )
        if not rows:
            return 0
        ids = [str(row["id"]) for row in rows]
        await self._store.execute_many(
            "UPDATE users SET status = 'disabled', revision = revision + 1 WHERE id = ?",
            [(user_id,) for user_id in ids],
        )
        await self._store.execute_many(
            "DELETE FROM sessions WHERE user_id = ?", [(user_id,) for user_id in ids]
        )
        return len(ids)

    # -------------------------------------------------------------- kadro

    async def sync(self) -> dict[str, Any]:
        """Kadroyu tazeler. `revision` DEĞİŞMEMİŞSE VERİ ÇEKİLMEZ (ADR 0021 §2).

        Merkez ulaşılamazsa hata YÜKSELTİLMEZ: senkron başarısız olduğunda
        kurulum eldeki önbellekle çalışmaya devam eder ve giriş bozulmaz (K7).
        """
        token = await self.installation_token()
        if token is None:
            return {"synced": False, "reason": "eşlenmemiş"}

        known = self.cache.revision()
        try:
            payload = await self._client.roster(token, known_revision=known)
        except IdentitySyncError as error:
            self._online = False
            self._online_checked_at = monotonic()
            log.warning("kadro çekilemedi", error=str(error))
            return {"synced": False, "reason": str(error)}

        self._online = True
        self._online_checked_at = monotonic()

        # PEPPER UYUŞMAZLIĞI BURADA YAKALANIR. Merkez her kadro yanıtında
        # anahtarının parmak izini gönderiyor; kurulumunki tutmuyorsa çekilen
        # satırlar HİÇBİR PIN'le eşleşmez ve kullanıcı yalnız "PIN yanlış"
        # görür — sebebi hiçbir yerde yazmadan. 17.08.2026'da tam olarak bu
        # yaşandı ve uzaktan teşhis edilemedi.
        await self._check_pepper(payload.get("pepperFingerprint"))

        if payload.get("changed") is False:
            return {"synced": True, "changed": False, "revision": known}

        self.cache.write(payload)
        return {"synced": True, "changed": True, "revision": payload.get("revision")}

    async def _check_pepper(self, uzak_izi: Any) -> None:
        """Merkezin anahtarıyla kurulumunki aynı mı?

        Yalnız İŞARETLER, hiçbir şeyi düzeltmez: sessizce yeni anahtar
        benimsemek, o makinede zaten çalışan kullanıcıların girişini kırardı.
        Kararı kullanıcı verir — ekran "yeniden eşlenmeli" der.
        """
        self.pepper_mismatch = False
        if not uzak_izi:
            return  # eski merkez sürümü — sessiz kal, eski davranış sürsün
        yerel = await self._vault.get("core.pin_pepper")
        if not yerel:
            return
        if pepper_fingerprint(yerel) != str(uzak_izi):
            self.pepper_mismatch = True
            log.error(
                "KİMLİK ANAHTARI UYUŞMUYOR — bu kurulumdaki hiçbir PIN çalışmaz, "
                "yeniden eşlenmesi gerekiyor",
                yerel=pepper_fingerprint(yerel), merkez=str(uzak_izi),
            )

    async def reset_pairing(self) -> dict[str, Any]:
        """Kurulumun eşlemesini SIFIRLAR — eşleme ekranı yeniden açılsın diye.

        NEDEN GİRİŞ İSTEMEZ. Bu yol tam da giriş yapılamadığında gerekiyor:
        anahtarı uyuşmayan bir kurulumda kimse giremez, dolayısıyla
        `unpair`in istediği oturum hiçbir zaman kurulamaz. Kilit buydu ve
        kurulum kendi kendini onaramıyordu.

        RİSKİ DAR: yalnız BU makinenin eşleme durumunu düşürür. Yeniden
        eşlenmek için merkezden yeni bir kod gerekir ve onu ancak yetkili biri
        üretebilir. Yerel kullanıcı kayıtlarına ve kasadaki öteki sırlara
        dokunulmaz; `private_key` de KALIR, makine yeniden eşlenince aynı
        kimlikle döner.
        """
        await self._vault.delete(TOKEN_KEY)
        await self._vault.delete(INSTALLATION_ID_KEY)
        # Anahtar "kendiliğinden" sayılır ki bir sonraki eşleme onu benimseyebilsin.
        # Bu satır olmadan sıfırlama işe yaramaz: `_adopt_pepper` "bu kurulumun
        # kendi anahtarı var" deyip reddeder ve kilit geri gelirdi.
        await self._vault.set(self._vault.AUTO_FLAG, "1")
        self.cache.clear()
        self.pepper_mismatch = False
        log.warning("kurulum eşlemesi sıfırlandı — eşleme ekranı yeniden açılacak")
        return {"reset": True}

    # -------------------------------------------------------------- yazma

    async def _assert_write_allowed(self) -> str:
        """Yazma yalnız çevrimiçidir (ADR 0021 §3).

        Bağlantı yoksa **istek hiç gönderilmez**; ekran "bu işlem için bağlantı
        gerekiyor" der. Yarım yazılmış bir kadro, hiç yazılmamıştan kötüdür.
        """
        if not self.configured:
            raise IdentitySyncError("Kimlik servisi ayarlanmamış.")
        token = await self.installation_token()
        if token is None:
            raise NotPaired("Bu kurulum merkezle eşlenmemiş.")
        if not await self._ensure_online():
            raise WriteRequiresConnection
        return token

    async def create_user(self, actor_id: str, body: dict[str, Any]) -> dict[str, Any]:
        token = await self._assert_write_allowed()
        return await self._client.create_user(token, actor_id, body)

    async def update_user(self, actor_id: str, user_id: str,
                          body: dict[str, Any]) -> dict[str, Any]:
        token = await self._assert_write_allowed()
        return await self._client.update_user(token, actor_id, user_id, body)

    async def set_status(self, actor_id: str, user_id: str, status: str) -> dict[str, Any]:
        token = await self._assert_write_allowed()
        return await self._client.set_status(token, actor_id, user_id, status)

    # ------------------------------------------------------------ denetim

    async def push_audit(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Denetim kayıtlarını merkeze iter (ADR 0021 §5).

        **ÖNCE KUYRUĞA YAZILIR, SONRA GÖNDERİLİR.** Sıra bilerek böyledir:
        gönderip başarısızlıkta yazmak, sürecin tam o anda ölmesi hâlinde kaydı
        düşürürdü. ADR §5 "asla düşürülmez" diyor.

        Kuyruk bağlıyken bu çağrı AĞ HATASI YÜKSELTMEZ: kayıt yerelde durur ve
        bir sonraki turda yeniden denenir. Eşlenmemiş kurulumda da kayıt
        birikir — eşleme yapıldığı anda kuyruk boşalır.

        Kuyruk bağlanmamışsa (`attach_store` hiç çağrılmadıysa) eski sözleşme
        geçerlidir: gönderilir ve hata YÜKSELİR. Sessizce yutmak, çağıranın
        kayıtları elinde tuttuğunu sanmasına yol açardı.
        """
        if self._queue is None:
            token = await self.installation_token()
            if token is None:
                raise NotPaired("Bu kurulum merkezle eşlenmemiş.")
            return await self._client.push_audit(token, entries)

        accepted = await self._queue.enqueue(entries)
        return {"accepted": accepted, **await self.flush_audit()}

    async def flush_audit(self, *, limit: int = BATCH_SIZE) -> dict[str, Any]:
        """Kuyrukta sırası gelmiş kayıtları göndermeyi dener.

        HATA YÜKSELTMEZ ve HİÇBİR KAYIT DÜŞMEZ: gönderilemeyen satırlar geri
        çekilmeli olarak ertelenir (`queue.backoff_seconds`). Dışarıdan da
        çağrılabilir — zamanlanmış bir tur, ağ döndüğünde kuyruğu boşaltır.
        """
        if self._queue is None:
            return {"sent": 0, "pending": 0, "reason": "kuyruk bağlanmamış"}

        pending = await self._queue.depth()
        token = await self.installation_token()
        if token is None:
            # Eşlenmemiş kurulumda kayıt BİRİKİR; eşleme yapılınca gider.
            return {"sent": 0, "pending": pending, "reason": "eşlenmemiş"}

        batch = await self._queue.due(limit)
        if not batch:
            return {"sent": 0, "pending": pending}

        try:
            await self._client.push_audit(token, [item["entry"] for item in batch])
        except IdentitySyncError as error:
            self._online = False
            self._online_checked_at = monotonic()
            await self._queue.defer(batch, str(error))
            log.warning("denetim kaydı gönderilemedi, kuyrukta kaldı",
                        pending=pending, error=str(error))
            return {"sent": 0, "pending": pending, "reason": str(error)}

        self._online = True
        self._online_checked_at = monotonic()
        await self._queue.drop([int(item["id"]) for item in batch])
        return {"sent": len(batch), "pending": await self._queue.depth()}

    async def audit_pending(self) -> int:
        """Kuyrukta bekleyen kayıt sayısı. Kuyruk yoksa 0."""
        return 0 if self._queue is None else await self._queue.depth()
