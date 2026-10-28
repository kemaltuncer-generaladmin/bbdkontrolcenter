"""ÜRETİLEN DOSYA — elle düzenlenmez.

Kaynak: bbd-akademi/docs/api/openapi.yaml (info.version 1.0.0-draft.3).
Üretici: modules/bbd_akademi/tools/gen_allowlist.py

Vekilin geçirdiği işlemlerin tamamı. Burada olmayan her istek 403'tür.
Akademi izni → KM izni eşlemesi `permission_map.py` içindedir. İşlemler alt
alan dosyalarındadır (ADR 0033 Aşama 2); burada üreticinin sırasıyla
(yol, yöntem) tek demette birleşir.
"""

from __future__ import annotations

from ..core.operations import Operation
from . import (
    admin,
    attendance,
    coaching,
    comms,
    contracts,
    exams,
    finance,
    lessons,
    recordings,
    reports,
    resources,
    students,
    today,
    work,
)

CONTRACT_VERSION = '1.0.0-draft.3'

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

OPERATIONS: tuple[Operation, ...] = tuple(sorted(
    (
        *admin.OPERATIONS,
        *attendance.OPERATIONS,
        *coaching.OPERATIONS,
        *comms.OPERATIONS,
        *contracts.OPERATIONS,
        *exams.OPERATIONS,
        *finance.OPERATIONS,
        *lessons.OPERATIONS,
        *recordings.OPERATIONS,
        *reports.OPERATIONS,
        *resources.OPERATIONS,
        *students.OPERATIONS,
        *today.OPERATIONS,
        *work.OPERATIONS,
    ),
    key=lambda operation: (operation.path, _METHODS.index(operation.method)),
))
