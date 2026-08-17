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
    await sync.fetch_provisioning()     → sırları kasaya, ayarları ayar deposuna yazar

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
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog

from km_core.config.loader import Config
from km_core.config.settings_store import SettingsStore

if TYPE_CHECKING:  # pragma: no cover — yalnız tip denetimi için
    from km_core.store.db import Store

from .cache import RosterCache
from .client import CLIENT_VERSION, IdentityClient, IdentityResponseError
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
# Merkezden çekilen kurulum paketinin revizyonu (ADR 0025). Kasada durur çünkü
# kasa zaten bu yeteneğin kalıcı defteridir; ayrı bir dosya açmak, eşleme
# çözülünce temizlenmesi unutulacak ikinci bir durum yaratırdı.
PROVISIONING_REVISION_KEY = "identity_sync.provisioning_revision"

# Çevrimiçilik bilgisinin tazelik süresi. Her yazmadan önce `/health` sormak
# gereksiz; hiç sormamak ise "ağ yoksa denemez" kuralını boşa çıkarır.
ONLINE_PROBE_TTL_SECONDS = 15.0

# MERKEZ BUNLARI GÖNDEREMEZ — gönderse de yazılmaz.
#
# `services/identity/app/provisioning.py` aynı yasağı YAZMA tarafında da
# uyguluyor; buradaki kopya gereksiz değil, İKİNCİ KAPIDIR (K9 ile aynı fikir).
# Yanlış yapılandırılmış ya da ele geçirilmiş bir merkez `identity_sync.*`
# gönderirse bütün kurulumlar tek kurulum token'ını paylaşır; `core.pin_pepper`
# gönderirse o makinedeki herkesin girişi bir anda kırılır ve düz PIN'ler
# hiçbir yerde saklanmadığı için geri getirilemez. Kimlik anahtarı eşlemeyle
# gelir (`_adopt_pepper`), paketle değil.
UNDISTRIBUTABLE_PREFIXES = ("identity_sync.",)
UNDISTRIBUTABLE_KEYS = frozenset({
    "core.pin_pepper",
    "core.pin_pepper_auto",
    "core.pin_pepper_previous",
})


def distributable(key: str) -> bool:
    """Merkezden gelen bu anahtar kasaya yazılabilir mi?"""
    if key in UNDISTRIBUTABLE_KEYS:
        return False
    return not any(key.startswith(prefix) for prefix in UNDISTRIBUTABLE_PREFIXES)


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
            # Merkezden alınan kurulum paketinin revizyonu (ADR 0025). `None`
            # = hiç alınmadı. SIR DÖNMEZ, yalnız sayı: "bu makine paketi aldı
            # mı" sorusu ekranda cevaplanabilmeli.
            "provisioningRevision": await self.provisioning_revision(),
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
        #
        # KURULUM PAKETİ DE BURADAN GELİR (ADR 0025): `sync()` kadroyu
        # tazeledikten sonra `fetch_provisioning()` çağırıyor, yani eşlenen
        # makine sırlarını ve modül ayarlarını aynı turda alır. Ayrı bir çağrı
        # eklemek aynı isteği iki kez yapardı.
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

        # ESKİ ANAHTAR SİLİNMEZ, SAKLANIR — ve eşleme REDDEDİLMEZ.
        #
        # Burada önce sert bir ret vardı: işaretsiz bir pepper görülünce eşleme
        # `IdentitySyncError` ile düşüyordu. İki nedenle yanlıştı:
        #
        #   · MERKEZ KODU ÇOKTAN YAKMIŞ OLUYOR. Ret yerelde, merkez `/pair`e
        #     200 döndükten SONRA gerçekleşiyor; tek kullanımlık kod harcanmış,
        #     merkezde öksüz bir kurulum satırı açılmış, yerelde tek bayt
        #     yazılmamış oluyordu. Her yeni kod aynı yerde ölüyordu ve kurulum
        #     ASLA düzelmiyordu.
        #   · İŞARETİN YOKLUĞU "bu anahtar kullanılıyor" demek DEĞİL. İşaret
        #     (`AUTO_FLAG`) 17.08.2026'da eklendi; ondan önce doğmuş her kasada
        #     pepper var, işaret yok. Yani ölçüt, korumak istediği durumu değil
        #     yalnızca "eski kurulum" olmayı ölçüyordu.
        #
        # Yeni davranış: merkezin anahtarı benimsenir, eskisi
        # `core.pin_pepper_previous` altında KALIR. Hiçbir şey geri
        # döndürülemez biçimde kaybolmaz (geri alma ekleyerek yapılır) ve
        # gerekirse eski anahtar elle geri konabilir.
        #
        # BEDELİ AÇIKÇA YAZILIR: eski anahtarla üretilmiş YEREL kullanıcılar
        # (örneğin ilk açılışın bootstrap yöneticisi) bu andan sonra giriş
        # yapamaz — `secret_lookup`ları artık tutmaz. Merkezden gelen kadro
        # çalışır; zaten eşlemenin amacı odur.
        if mevcut is not None:
            await self._vault.set("core.pin_pepper_previous", mevcut)
            log.warning(
                "ÖNCEKİ KİMLİK ANAHTARI SAKLANDI — bu anahtarla üretilmiş YEREL "
                "kullanıcılar artık giriş yapamaz; merkezden gelen kadro çalışır",
                onceki=pepper_fingerprint(mevcut), yeni=pepper_fingerprint(pepper),
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
        # PAKET REVİZYONU DÜŞER, PAKETİN KENDİSİ KALIR (ADR 0025).
        #
        # Dağıtılmış sırları silmek, eşlemeyi yeniden kurmak isteyen yöneticinin
        # elindeki makineyi çalışmaz hâle getirirdi: geçitler o an ölür ve geri
        # getirmenin yolu 17 sırrı elle girmektir. Revizyon işaretinin düşmesi
        # yeter — makine yeniden eşlendiğinde paketi baştan çeker ve tazelenir.
        #
        # BEDELİ AÇIKÇA YAZILIR: eşlemesi çözülmüş bir makinede iş sırları
        # kasada durmaya devam eder. Kullanıcının tehdit modeli bunu kabul
        # ediyor (kurum içi, fiziksel denetim); kaybolan cihazın çaresi
        # `unpair` değil, merkezden iptal ve yerinde silmedir.
        await self._vault.delete(PROVISIONING_REVISION_KEY)

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
        except IdentityResponseError as error:
            # TOKEN ARTIK GEÇERSİZ (401/403) — kurulum merkezden İPTAL EDİLMİŞ.
            #
            # BU DAL AYRI DURUR ve ayrı durmak zorunda: iptal edilmiş bir
            # kurulum "ağ yok" değildir. Ağ hatası gibi ele alınırsa makine
            # sonsuza dek eldeki bayat kadroyla çalışmaya devam eder, eşleme
            # ekranı hiç açılmaz ve kimse giremez — kullanıcı da neden
            # giremediğini hiçbir yerde göremez. 17.08.2026'da bir MacBook tam
            # olarak bu duruma düştü: token iptal edilmişti, kadro çekilemiyordu,
            # ve `paired: true` olduğu için eşleme ekranı bir daha hiç gelmedi.
            #
            # Kurulum bu durumda kendi eşlemesini düşürür: bir sonraki açılışta
            # EŞLEME EKRANI gelir ve makine yeniden eşlenebilir. Yerel
            # kullanıcılara ve kasadaki öteki sırlara dokunulmaz.
            if error.status in (401, 403):
                log.error(
                    "kurulum token'ı merkezde geçersiz — eşleme sıfırlanıyor",
                    status=error.status,
                )
                await self.reset_pairing()
                return {"synced": False, "reason": "kurulum iptal edilmiş", "reset": True}
            self._online = False
            self._online_checked_at = monotonic()
            log.warning("kadro çekilemedi", error=str(error))
            return {"synced": False, "reason": str(error)}
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

        # KURULUM PAKETİ HER TURDA SORULUR (ADR 0025) — kadro değişmemiş olsa
        # bile. İki defter ayrıdır: bir sırrın döndürülmesi kadroyu
        # ilgilendirmez. `changed is False` dalının ÜSTÜNDE durmasının sebebi
        # budur; altına konsaydı, kadrosu sabit bir kurulum yeni sırrı hiç
        # görmezdi.
        paket = await self.fetch_provisioning()

        if payload.get("changed") is False:
            return {"synced": True, "changed": False, "revision": known,
                    "provisioning": paket}

        self.cache.write(payload)
        return {"synced": True, "changed": True, "revision": payload.get("revision"),
                "provisioning": paket}

    # ------------------------------------------------------ kurulum paketi

    async def provisioning_revision(self) -> int | None:
        """Kasadaki paket revizyonu. Hiç çekilmemişse `None`."""
        raw = await self._vault.get(PROVISIONING_REVISION_KEY)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:  # pragma: no cover — kasaya elle yazılmış bozuk değer
            return None

    async def fetch_provisioning(self) -> dict[str, Any]:
        """Merkezdeki kurulum paketini çeker ve UYGULAR (ADR 0025).

        Sırlar KASAYA, modül ayarları ÇEKİRDEK AYAR DEPOSUNA yazılır. İkisi
        ayrı yere gider çünkü ayrı şeylerdir: kasa şifreler ve ekranda değer
        göstermez, ayar deposu düz durur ve Sistem Ayarları ekranında görünür
        (K8 — sır ayar deposuna yazılmaz).

        **HATA YÜKSELTMEZ** (K7). Paket çekilemezse kurulum eldeki değerlerle
        çalışmaya devam eder; kimlik ve giriş bundan etkilenmez. Merkez eski
        sürümse (uç yok → 404) bu bir arıza değildir, sessizce geçilir.

        `known_revision` gönderilir: değişmemişse sırlar ağa HİÇ ÇIKMAZ.
        """
        token = await self.installation_token()
        if token is None:
            return {"applied": False, "reason": "eşlenmemiş"}

        known = await self.provisioning_revision()
        try:
            payload = await self._client.provisioning(token, known_revision=known)
        except IdentityResponseError as error:
            if error.status == 404:
                # Merkez bu ucu tanımıyor — 0025 öncesi sürüm. Kurulum bugünkü
                # gibi kendi kasasıyla çalışır; uyarı bile gereksizdir.
                return {"applied": False, "reason": "merkezde kurulum paketi ucu yok"}
            log.warning("kurulum paketi çekilemedi", status=error.status, error=str(error))
            return {"applied": False, "reason": str(error)}
        except IdentitySyncError as error:
            log.warning("kurulum paketi çekilemedi", error=str(error))
            return {"applied": False, "reason": str(error)}

        if payload.get("changed") is False:
            return {"applied": False, "changed": False, "revision": known}

        secrets = payload.get("secrets") or {}
        settings = payload.get("settings") or {}
        try:
            yazilan_sir = await self._apply_secrets(dict(secrets))
            yazilan_ayar = await self._apply_settings(dict(settings))
        except Exception as error:  # noqa: BLE001 — K7, gerekçe aşağıda
            # UYGULAMA PATLARSA REVİZYON YAZILMAZ ve bir sonraki tur yeniden
            # dener. Hata sınıfı dar tutulamaz: kasa (disk, izin, bozuk anahtar)
            # ve ayar deposu (eksik tablo, kilitli veritabanı) birbirinden çok
            # farklı türler atar ve HEPSİNİN buradaki karşılığı aynıdır — bu
            # tur olmadı, kimlik ve giriş bundan etkilenmemeli (K7).
            #
            # Sessiz yutulmaz: `error` seviyesinde loga düşer ve çağırana
            # sebebiyle döner.
            log.error("kurulum paketi uygulanamadı", error=str(error))
            return {"applied": False, "reason": str(error)}

        revision = payload.get("revision")
        if revision is not None:
            await self._vault.set(PROVISIONING_REVISION_KEY, str(int(revision)))

        log.info("kurulum paketi uygulandı", revision=revision,
                 secrets=yazilan_sir, settings=yazilan_ayar)
        return {
            "applied": True,
            "changed": True,
            "revision": revision,
            "secrets": yazilan_sir,
            "settings": yazilan_ayar,
        }

    async def _apply_secrets(self, secrets: dict[str, Any]) -> int:
        """Sırları kasaya yazar; DEĞİŞMEYENE DOKUNMAZ. Dönüş: yazılan sayısı.

        Değişmeyeni atlamak yalnız hız değil: kasa yazması `updated_at` alanını
        tazeliyor ve her senkron turunda 17 satırı yenilemek, "bu sır ne zaman
        değişti" sorusunu cevapsız bırakırdı.

        Yasaklı anahtar GELİRSE YAZILMAZ ve uyarı düşer (`distributable`).
        """
        yazilan = 0
        for key, value in secrets.items():
            if not distributable(key):
                log.error("merkez dağıtılamaz bir anahtar gönderdi, YAZILMADI", key=key)
                continue
            metin = value if isinstance(value, str) else str(value)
            if await self._vault.get(key) == metin:
                continue
            await self._vault.set(key, metin)
            yazilan += 1
        return yazilan

    async def _apply_settings(self, settings: dict[str, Any]) -> int:
        """Modül ayarlarını çekirdek ayar deposuna yazar (ADR 0018 §4).

        **DEPO BAĞLANMAMIŞSA HİÇBİR ŞEY YAPILMAZ** (K7): yetenek `Vault` ve
        `Config` ile kuruluyor, çekirdek deposunu `attach_store` ile sonradan
        görüyor. Bağlanmamış bir depoda patlamak, eşleme akışının tamamını
        düşürürdü.

        **AYAR HEMEN ETKİLİ OLMAZ.** Modül geçitleri adreslerini kurulurken
        okuyor (`modules/*/backend/module.py` → `setup`) ve ayar katmanı
        açılışta uygulanıyor (`km_core/http/app.py`). Yani ilk eşlemeden sonra
        geçitler BİR SONRAKİ AÇILIŞTA çalışır. Bu yeni bir davranış değil:
        Sistem Ayarları ekranından değiştirilen ayar da aynı yolu izler
        (ADR 0018 §4). Ayrıntı ve gerekçe: ADR 0025 — açık kalan kapılar.

        SIR BURAYA YAZILMAZ (K8). Merkez sırrı `secrets` sözlüğünde gönderir;
        bu sözlük yalnız ayar taşır ve değerleri Sistem Ayarları ekranında
        GÖRÜNÜR.
        """
        if self._store is None:
            log.warning("ayar deposu bağlanmamış; modül ayarları uygulanmadı",
                        count=len(settings))
            return 0

        # `SettingsStore` bir `Store` bekler; buradaki nesne kuyruğun dar
        # protokolüyle tutuluyor (`QueueStore`) ve çalışma anında zaten o
        # `Store`dur. Protokolü genişletmek yerine dönüştürmek, kuyruğun sahte
        # bir depoyla test edilebilir kalmasını bozmaz.
        store = SettingsStore(cast("Store", self._store))
        mevcut = await store.values()
        yazilan = 0
        for key, value in settings.items():
            if key in mevcut and mevcut[key] == value:
                continue
            await store.put(key, value, actor_id=None)
            yazilan += 1
        return yazilan

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
        # Paket revizyonu düşer; sırlar kalır (`unpair` ile aynı gerekçe).
        await self._vault.delete(PROVISIONING_REVISION_KEY)
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
