"""Netgsm sağlayıcısı.

Resmi ``netgsm-sms`` SDK'sını sarmalar. Bulgular ve tuzaklar:
docs/netgsm-integration.md · Karar: ADR 0010
"""

from .adapter import NetgsmConfig, NetgsmSmsProvider

__all__ = ["NetgsmConfig", "NetgsmSmsProvider"]
