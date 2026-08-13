"""km_sdk — modüllerin görebildiği TEK paket (K2).

Modül `km_core` ya da `km_platform` import etmez; buradan geçer. Bu katman
sözleşmedir: çekirdek içi yeniden düzenleme modülleri kırmaz.

Kullanım:

    from km_sdk import ModuleContext, APIRouter

    def register(ctx: ModuleContext) -> None:
        router = APIRouter()
        ...
        ctx.add_router(router)
"""

from __future__ import annotations

# FastAPI türleri modüllerin router yazabilmesi için buradan yeniden dışa
# vurulur; modül doğrudan çekirdeğe bakmak zorunda kalmaz.
#
# `Query`: 20 mağaza ekranının hepsi süzgeçli liste çekiyor. Query parametresi
# olmadan filtreler ya gövdeye taşınır (GET'te yanlış) ya da çıplak imza
# parametresi olarak yazılır (doğrulama ve belge kaybolur).
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from km_core.contracts.module import ModuleContext, ModuleStore

# Diske yazma: kişisel veri taşıyan çıktılar (PDF, CSV, yedek) yalnız kullanıcıya
# okunur olmalı. Modül `write_bytes` çağırırsa umask'a kalır ve 0644 çıkar.
from km_core.files.outputs import desktop_dir, report_dir, reports_root
from km_core.files.private import ensure_private_dir, write_private

# Rapor biçimlendirme. Modül modülü import edemediği için (K3) bu üreteç
# çekirdektedir; aksi hâlde her rapor üreten modüle 250 satır kopyalanırdı.
from km_core.files.reports import (
    ExportError,
    build_pdf,
    csv_bytes,
    money,
    number,
    percent,
)
from km_core.http.deps import requires
from km_core.security.identity import CurrentUser

# Platform yeteneklerinin VERİ TÜRLERİ de buradan görünür. Modül `scheduler`
# yeteneğine tetikleyici verirken tipli bir sözleşmeye ihtiyaç duyar; ham sözlük
# göndermek yazım hatasını sessiz bırakır.
from km_platform.notify import (
    DeliveryReport,
    DeliveryStatus,
    NotifyError,
    SmsConfigError,
    SmsError,
    SmsInvalidRecipient,
    SmsMessage,
    SmsProvider,
    SmsRateLimited,
    SmsRejected,
    SmsResult,
    TextPlan,
    normalize_msisdn,
    plan_text,
)
from km_platform.notify.text import offending, simplify
from km_platform.scheduler.weekly import Trigger

__version__ = "0.1.0"

__all__ = [
    "APIRouter",
    "BaseModel",
    "CurrentUser",
    "DeliveryReport",
    "DeliveryStatus",
    "ExportError",
    "Field",
    "HTTPException",
    "ModuleContext",
    "ModuleStore",
    "NotifyError",
    "Query",
    "SmsConfigError",
    "SmsError",
    "SmsInvalidRecipient",
    "SmsMessage",
    "SmsProvider",
    "SmsRateLimited",
    "SmsRejected",
    "SmsResult",
    "TextPlan",
    "Trigger",
    "__version__",
    "build_pdf",
    "csv_bytes",
    "desktop_dir",
    "ensure_private_dir",
    "money",
    "normalize_msisdn",
    "number",
    "offending",
    "percent",
    "plan_text",
    "report_dir",
    "reports_root",
    "requires",
    "simplify",
    "write_private",
]
