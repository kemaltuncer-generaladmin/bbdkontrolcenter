"""`notify` yeteneğinin dış yüzü.

Modül sağlayıcıyı seçmez, kimlik bilgisini görmez, SDK'yı import etmez (K4).
Yalnız `ctx.capability("notify")` alır ve `SmsProvider` protokolüyle konuşur;
Netgsm'den başka bir sağlayıcıya geçilirse tek satır modül kodu değişmez.

AÇILMASI İKİ ADIM İSTER — ikisi de bilinçli sürtünmedir:
  1. `config: platform.notify.sms.enabled: true`
  2. `dry_run: false`

Varsayılan ikisi de kapalıdır. Kimlik bilgisi girilmiş bir kurulumda bile,
`dry_run` açıkken tek bir gerçek SMS gitmez. "Yanlışlıkla 1800 kişiye mesaj"
hatasının önündeki asıl engel budur.

Kimlik bilgileri kasadan gelir (K8):
    secrets:
      notify.netgsm.username: "..."
      notify.netgsm.password: "..."
      notify.netgsm.header:   "BBDUNYAM"   # ayardan da verilebilir
"""

from __future__ import annotations

from typing import Any

from .contracts import SmsProvider
from .errors import SmsConfigError

#: Kasa anahtarları. Başlık hem kasadan hem ayardan gelebilir: gizli değildir,
#: ama kurulumu tek yerde toplamak isteyenler kasaya koyabilsin.
SECRET_USERNAME = "notify.netgsm.username"
SECRET_PASSWORD = "notify.netgsm.password"
SECRET_HEADER = "notify.netgsm.header"


class NotifyService:
    """SMS sağlayıcısını ayardan kurar ve tekil olarak tutar."""

    def __init__(self, vault: Any, config: Any, log: Any) -> None:
        self._vault = vault
        self._config = config
        self._log = log
        self._provider: SmsProvider | None = None

    # ----------------------------------------------------------- ayar

    def _settings(self) -> dict[str, Any]:
        section = self._config.section("platform.notify") or {}
        sms = section.get("sms") or {}
        return sms if isinstance(sms, dict) else {}

    @property
    def enabled(self) -> bool:
        return bool(self._settings().get("enabled", False))

    @property
    def dry_run(self) -> bool:
        """Varsayılan AÇIK. Kapatmak açık bir karardır."""
        return bool(self._settings().get("dry_run", True))

    @property
    def provider_name(self) -> str:
        return str(self._settings().get("provider") or "netgsm")

    # -------------------------------------------------------- sağlayıcı

    async def sms(self) -> SmsProvider:
        """Yapılandırılmış SMS sağlayıcısı.

        Raises:
            SmsConfigError: yetenek kapalı, sağlayıcı bilinmiyor ya da kimlik
                bilgisi eksik.
        """
        if self._provider is not None:
            return self._provider

        if not self.enabled:
            raise SmsConfigError(
                "SMS kapalı. Açmak için: config/local.yaml → "
                "platform.notify.sms.enabled: true"
            )

        name = self.provider_name
        if name != "netgsm":
            raise SmsConfigError(f"Bilinmeyen SMS sağlayıcısı: {name!r}. Beklenen: netgsm.")

        settings = self._settings()
        username = await self._vault.get(SECRET_USERNAME)
        password = await self._vault.get(SECRET_PASSWORD)
        header = str(settings.get("header") or "") or (await self._vault.get(SECRET_HEADER) or "")

        if not username or not password:
            raise SmsConfigError(
                "Netgsm kimlik bilgisi yok. config/local.yaml → secrets: "
                f"{SECRET_USERNAME} ve {SECRET_PASSWORD} girilmeli."
            )

        # SDK import'u BURADA: paket kurulu değilse çekirdek yine ayağa kalkar,
        # yalnız SMS gönderen uç anlaşılır hata döner (K7).
        from .providers.netgsm import NetgsmConfig, NetgsmSmsProvider

        config = NetgsmConfig(
            username=str(username),
            password=str(password),
            header=str(header),
            appname=str(settings.get("appname") or "") or None,
            iys_filter=str(settings.get("iys_filter") or "0"),
            timeout=float(settings.get("timeout_seconds") or 30.0),
            dry_run=self.dry_run,
        )
        self._provider = NetgsmSmsProvider(config)
        self._log.info(
            "sms sağlayıcısı hazır",
            provider=name, header=config.header, dry_run=config.dry_run,
        )
        return self._provider

    # ------------------------------------------------------------ durum

    async def ready(self) -> dict[str, Any]:
        """Ekranın gösterebileceği durum. **Hata fırlatmaz** (K7).

        Kimlik bilgisi yokken de bir cevap döner; ekran "SMS kurulmamış" der,
        beyaz sayfa göstermez.
        """
        state: dict[str, Any] = {
            "provider": self.provider_name,
            "enabled": self.enabled,
            "dryRun": self.dry_run,
            "configured": False,
            "header": "",
            "error": "",
        }
        try:
            provider = await self.sms()
        except SmsConfigError as failure:
            state["error"] = str(failure)
            return state
        except Exception as failure:  # noqa: BLE001 - durum ekranı ayakta kalmalı
            state["error"] = f"SMS katmanı kurulamadı: {failure}"
            self._log.warning("sms hazırlık hatası", error=str(failure))
            return state

        state["configured"] = True
        state["header"] = getattr(getattr(provider, "_config", None), "header", "")
        return state
