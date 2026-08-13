"""notify — bildirim kanalları (SMS, e-posta, webhook).

Genel yüzey buradan dışa vurulur. Sağlayıcıya özgü paketler (providers/*)
doğrudan import edilmez; sağlayıcı ayardan seçilir ve ``SmsProvider``
protokolü üzerinden kullanılır.
"""

from .contracts import (
    DeliveryReport,
    DeliveryStatus,
    SmsMessage,
    SmsProvider,
    SmsResult,
)
from .errors import (
    NotifyError,
    SmsAuthError,
    SmsConfigError,
    SmsError,
    SmsInvalidRecipient,
    SmsProviderError,
    SmsRateLimited,
    SmsRejected,
    SmsTransportError,
)
from .text import TextPlan, normalize_msisdn, offending, plan_text, simplify

__all__ = [
    "DeliveryReport",
    "DeliveryStatus",
    "NotifyError",
    "SmsAuthError",
    "SmsConfigError",
    "SmsError",
    "SmsInvalidRecipient",
    "SmsMessage",
    "SmsProvider",
    "SmsProviderError",
    "SmsRateLimited",
    "SmsRejected",
    "SmsResult",
    "SmsTransportError",
    "TextPlan",
    "normalize_msisdn",
    "offending",
    "plan_text",
    "simplify",
]
