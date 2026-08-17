"""Netgsm SMS sağlayıcı uygulaması.

Resmi ``netgsm-sms`` SDK'sını sarmalar. Sarmalayıcının var olma nedenleri
(ayrıntı: docs/netgsm-integration.md):

  1. SDK yanıt gövdesindeki ``code`` alanını başarı yolunda denetlemez —
     gönderilmemiş bir SMS başarılı görünür. Burada her yanıt denetlenir.
  2. SDK üç ayrı tarih biçimi bekler; burada yalnızca ``datetime`` kabul
     edilir, biçimlendirme uç noktaya göre yapılır.
  3. SDK senkron ``requests`` kullanır; çağrılar iş parçacığına taşınır.
  4. SDK zaman aşımını sabit kodlar (30 sn, ayarlanamaz); burada kendi
     zaman aşımımız uygulanır.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from netgsm import Netgsm
from netgsm.exceptions.api_exception import (
    ApiException,
    ConnectionException,
    ServerException,
    TimeoutException,
    UnauthorizedException,
)

from ...contracts import (
    DeliveryReport,
    SmsMessage,
    SmsResult,
)
from ...errors import (
    SmsAuthError,
    SmsConfigError,
    SmsError,
    SmsInvalidRecipient,
    SmsProviderError,
    SmsTransportError,
)
from ...text import normalize_msisdn, plan_text
from . import codes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NetgsmConfig:
    """Netgsm bağlantı ayarı.

    Kimlik bilgileri kasadan gelir; ayar dosyasına yazılmaz (K8).
    """

    username: str
    password: str
    #: Netgsm'de tanımlı gönderici başlığı. Tanımsız başlık kod 40 ile reddedilir.
    header: str
    appname: str | None = None
    api_url: str | None = None
    #: İşletimsel uyarılar bilgilendirme içeriklidir (docs/netgsm-integration.md).
    iys_filter: str = codes.IysFilter.INFORMATIONAL
    #: Saniye. SDK'nın sabit 30 sn'lik değerinin yerine geçer.
    timeout: float = 30.0
    #: True iken istek sağlayıcıya GÖNDERİLMEZ; doğrulama ve parça hesabı yapılır.
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise SmsConfigError("Netgsm kullanıcı adı ve parolası zorunlu.")
        if not self.header:
            raise SmsConfigError(
                "Gönderici başlığı (msgheader) zorunlu. "
                "Netgsm'de tanımlı olmayan başlık kod 40 ile reddedilir."
            )


class NetgsmSmsProvider:
    """``SmsProvider`` protokolünün Netgsm uygulaması."""

    def __init__(self, config: NetgsmConfig) -> None:
        self._config = config
        self._client = Netgsm(
            username=config.username,
            password=config.password,
            api_url=config.api_url,
            appname=config.appname,
        )

    # ------------------------------------------------------------ gönderim

    async def send(
        self,
        messages: Sequence[SmsMessage],
        *,
        header: str | None = None,
        scheduled_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> SmsResult:
        if not messages:
            return SmsResult(
                accepted=True, job_id=None, recipients=0, parts=0, dry_run=self._config.dry_run
            )

        # 1) Numaraları normalleştir ve kodlamayı planla — sağlayıcıya
        #    gitmeden önce. Geçersiz numara için para harcanmaz.
        payload: list[dict[str, str]] = []
        total_parts = 0
        needs_turkish = False

        for msg in messages:
            try:
                number = normalize_msisdn(msg.to)
            except ValueError as exc:
                raise SmsInvalidRecipient(str(exc), raw=msg.to) from exc

            plan = plan_text(msg.text)
            total_parts += plan.parts
            if plan.encoding == "tr":
                needs_turkish = True
            if plan.unicode:
                log.warning(
                    "SMS metni GSM-7'ye sığmıyor, UCS-2'ye düşülüyor: "
                    "parça başına 70 karakter, %d parça",
                    plan.parts,
                )

            payload.append({"msg": msg.text, "no": number})

        # Netgsm toplu gönderimde TEK kodlama kabul eder: partiden herhangi
        # biri Türkçe karakter içeriyorsa tümü 'tr' ile gider.
        encoding = "tr" if needs_turkish else None

        if self._config.dry_run:
            log.info(
                "KURU ÇALIŞMA — SMS gönderilmedi. alıcı=%d parça=%d kodlama=%s",
                len(payload),
                total_parts,
                encoding or "gsm7",
            )
            return SmsResult(
                accepted=True,
                job_id="dry-run",
                recipients=len(payload),
                parts=total_parts,
                encoding=encoding,
                dry_run=True,
            )

        kwargs: dict[str, Any] = {
            "msgheader": header or self._config.header,
            "messages": payload,
            "iysfilter": self._config.iys_filter,
        }
        if encoding:
            kwargs["encoding"] = encoding
        if scheduled_at:
            kwargs["startdate"] = codes.fmt_send(scheduled_at)
        if expires_at:
            kwargs["stopdate"] = codes.fmt_send(expires_at)

        response = await self._call(self._client.sms.send, **kwargs)

        # 2) SDK'nın atlamadığı denetim: gövdedeki kod.
        codes.raise_for_code(response.get("code"), context="SMS gönderimi")

        return SmsResult(
            accepted=True,
            job_id=str(response.get("jobid")) if response.get("jobid") else None,
            recipients=len(payload),
            parts=total_parts,
            encoding=encoding,
            provider_code=str(response.get("code")) if response.get("code") else None,
            raw=response,
        )

    # -------------------------------------------------------------- iptal

    async def cancel(self, job_id: str) -> bool:
        if self._config.dry_run:
            log.info("KURU ÇALIŞMA — iptal isteği gönderilmedi: %s", job_id)
            return True

        response = await self._call(self._client.sms.cancel, jobid=job_id)
        codes.raise_for_code(response.get("code"), context=f"İptal ({job_id})")
        return True

    # ------------------------------------------------------------ başlıklar

    async def headers(self) -> list[str]:
        response = await self._call(self._client.sms.get_headers)
        codes.raise_for_code(response.get("code"), context="Başlık listesi")

        raw = response.get("msgheader") or response.get("msgheaders") or []
        if isinstance(raw, str):
            return [raw]
        return [str(h) for h in raw]

    # --------------------------------------------------------------- rapor

    async def report(
        self,
        start: datetime,
        end: datetime,
        *,
        job_ids: Sequence[str] | None = None,
    ) -> list[DeliveryReport]:
        """Teslim raporlarını sorgular.

        Not: sağlayıcı bu uç noktayı **dakikada 10 sorguyla** sınırlar;
        aşılırsa kod 80 döner (``SmsRateLimited``).
        """
        kwargs: dict[str, Any] = {
            "startdate": codes.fmt_report(start),
            "stopdate": codes.fmt_report(end),
        }
        if job_ids:
            kwargs["jobids"] = list(job_ids)

        # Kod 60 "ölçütünüze uyan kayıt yok" demektir — hata değil, boş sonuç.
        # Sağlayıcı bunu HTTP 406 ile de döndürebildiği için iki yolda da
        # yakalanır.
        try:
            response = await self._call(self._client.sms.get_report, **kwargs)
        except SmsError as exc:
            if exc.provider_code == "60":
                return []
            raise

        if str(response.get("code")) == "60":
            return []
        codes.raise_for_code(response.get("code"), context="Rapor sorgusu")

        reports: list[DeliveryReport] = []
        for row in response.get("messages") or response.get("report") or []:
            reports.append(
                DeliveryReport(
                    job_id=str(row.get("jobid", "")),
                    to=str(row.get("no", "")),
                    status=codes.to_status(row.get("status")),
                    raw=row,
                )
            )
        return reports

    # ------------------------------------------------------------- dahili

    async def _call(self, fn: Any, **kwargs: Any) -> dict[str, Any]:
        """Senkron SDK çağrısını iş parçacığında ve zaman aşımıyla çalıştırır.

        Uyarı: zaman aşımında ``asyncio.to_thread`` iş parçacığını durduramaz;
        istek arka planda tamamlanabilir. Bu yüzden zaman aşımı
        ``SmsTransportError`` olarak yükselir — "gitti mi bilinmiyor" anlamında.
        Yeniden denemeden önce rapor sorgusuyla doğrulanmalıdır.
        """
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, **kwargs),
                timeout=self._config.timeout,
            )
        except TimeoutError as exc:
            raise SmsTransportError(
                f"Netgsm yanıt vermedi ({self._config.timeout} sn). "
                "İsteğin ulaşıp ulaşmadığı bilinmiyor; yeniden denemeden önce "
                "rapor sorgusuyla doğrulayın."
            ) from exc
        except Exception as exc:  # SDK istisnaları burada tipli hataya çevrilir
            raise _translate(exc) from exc


def _translate(exc: Exception) -> SmsError:
    """SDK istisnasını sağlayıcıdan bağımsız hataya çevirir."""
    if isinstance(exc, (TimeoutException, ConnectionException)):
        return SmsTransportError(str(exc))
    if isinstance(exc, UnauthorizedException):
        return SmsAuthError(str(exc), provider_code=getattr(exc, "code", None))
    if isinstance(exc, ServerException):
        return SmsProviderError(str(exc), provider_code=getattr(exc, "code", None))
    if isinstance(exc, ApiException):
        code = getattr(exc, "code", None)
        if code and str(code) in codes.ERROR_MAP:
            exc_type, description = codes.ERROR_MAP[str(code)]
            return exc_type(description, provider_code=str(code))
        return SmsProviderError(str(exc), provider_code=str(code) if code else None)
    if isinstance(exc, SmsError):
        return exc
    return SmsProviderError(f"Beklenmeyen sağlayıcı hatası: {exc}")
