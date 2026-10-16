"""Rapor çıktısı — PDF ve Excel uyumlu CSV.

ORTAK ÜRETİCİYE TAŞINDI (63-finans-pdf, 16.10.2026). Bu dosya eskiden
reportlab ile kendi PDF üreticisini taşıyordu; aynı kod çekirdeğe
(`km_core/files/reports.py`) kopyalanmış ve iki kopya ayrı ayrı yaşıyordu.
Finansal raporların hepsi artık TEK şablondan çıkar (süzgeç başlığı,
hazırlayan, "Sayfa X / Y", ara/genel toplam, KVKK maskelemesi) ve modül onu
`km_sdk` üzerinden alır (K2). Davranış korunur: aynı bölüm türleri, aynı
para biçimi (`12.345,67 ₺`), aynı CSV kuralı (UTF-8 BOM + `;`), reportlab
yoksa anlaşılır hata (K7).

Ad bu dosyada kalır ki servis ve testler (`from .export import …`) değişmeden
çalışsın.
"""

from __future__ import annotations

from km_sdk import ExportError, build_pdf, csv_bytes, money

__all__ = ["ExportError", "build_pdf", "csv_bytes", "money"]
