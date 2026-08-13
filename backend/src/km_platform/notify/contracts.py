"""SMS sağlayıcı sözleşmesi.

Netgsm bir uygulamadır, sözleşme değil. Üst katmanlar bu protokole bakar;
sağlayıcı değişirse (veya ikinci bir sağlayıcı eklenirse) çağıran taraf
değişmez.

Bu tipler ileride ``km_sdk`` üzerinden modüllere açılacaktır — modüller
sağlayıcı paketini hiçbir zaman doğrudan görmez (K4, tek kapı).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Protocol


class DeliveryStatus(IntEnum):
    """Netgsm rapor durum kodlarının sağlayıcıdan bağımsız karşılığı."""

    NOT_DELIVERED = 0
    DELIVERED = 1
    PENDING = 2
    TIMEOUT = 3
    ERROR = 4
    UNREACHABLE = 11
    PROVIDER_ERROR = 13
    UNKNOWN = 99


@dataclass(frozen=True)
class SmsMessage:
    """Tek bir alıcıya gidecek mesaj.

    ``to`` serbest biçimde verilebilir; sağlayıcı katmanı normalleştirir.
    """

    to: str
    text: str


@dataclass(frozen=True)
class SmsResult:
    """Gönderim isteğinin sonucu.

    ``accepted`` yalnızca sağlayıcının işi **kabul ettiğini** söyler; teslim
    edildiği anlamına gelmez. Teslim durumu ``report()`` ile sorgulanır.
    """

    accepted: bool
    job_id: str | None
    recipients: int
    #: Tüm alıcılar toplamında tahmini parça sayısı — faturalanacak birim.
    #: Mesajlar farklı uzunlukta olabileceği için toplanarak hesaplanır.
    parts: int
    #: Toplu gönderimde kullanılan kodlama. None ise parametre gönderilmedi.
    encoding: str | None = None
    dry_run: bool = False
    provider_code: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DeliveryReport:
    """Tek bir alıcı için teslim raporu."""

    job_id: str
    to: str
    status: DeliveryStatus
    delivered_at: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)


class SmsProvider(Protocol):
    """SMS sağlayıcısının sağlaması gereken yüzey.

    Tüm metotlar async'tir. Senkron bir SDK sarmalanıyorsa çağrı bir iş
    parçacığına taşınır — olay döngüsü bloklanmaz.
    """

    async def send(
        self,
        messages: Sequence[SmsMessage],
        *,
        header: str | None = None,
        scheduled_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> SmsResult:
        """Mesajları gönderir.

        Raises:
            SmsInvalidRecipient: numara normalleştirilemedi (istek gitmedi).
            SmsConfigError: gönderici başlığı veya kimlik eksik.
            SmsAuthError, SmsRejected, SmsRateLimited, SmsProviderError,
            SmsTransportError: sağlayıcı yanıtına göre.
        """
        ...

    async def cancel(self, job_id: str) -> bool:
        """Zamanlanmış bir gönderimi iptal eder."""
        ...

    async def headers(self) -> list[str]:
        """Abonelikte tanımlı gönderici başlıklarını listeler."""
        ...

    async def report(
        self,
        start: datetime,
        end: datetime,
        *,
        job_ids: Sequence[str] | None = None,
    ) -> list[DeliveryReport]:
        """Teslim raporlarını sorgular.

        Not: sağlayıcı bu uç noktayı dakikada 10 sorguyla sınırlar.
        """
        ...
