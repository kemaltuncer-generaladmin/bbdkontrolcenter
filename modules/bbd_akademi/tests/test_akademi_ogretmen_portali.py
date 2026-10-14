"""Öğretmen portalı tam açık (iş 18-ogretmen-portali; README §14 madde 63) — KM kurum ayarları ekranı.

Kullanıcı: "Öğretmen yetkisini de full açalım." · "Yönetici de tüm rollerin yapabildiği şeyleri yapabilsin."

- Ayarlar → Kurum → "Öğretmen / koç" kartı: `teacher.scope` seçici (`related` | `all`); değişince yalnız
  `teacher` grubu `admin_patchSettings` ile gider (kısmi güncelleme).
- Özellikler → "Öğretmen / koç portalı" açıklaması yer tutucu değil, sekmeleri anlatır.
- Personel ekranı ipucu öğretmen kapsamı ayarına yönlendirir ("uygulama erişimi" ile tutarlı).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.bbd_akademi.tests.test_akademi_ogretmen_koc import (  # ortak taklit Akademi + panel
    PRELUDE,
    node_run,
)

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

SCOPE = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  let settings = { name: 'Benim Başarı Dünyam', shortName: 'BBD', timezone: 'Europe/Istanbul', address: null,
    support: { phone: null, whatsapp: null, email: null, hours: null },
    quietHoursDefault: { start: '22:00', end: '07:30' },
    finance: { receiptPrefix: 'AK', bookChargeDueDays: 30, reminderDaysBefore: [3, 1], overdueReminderEveryDays: 7 },
    books: { requestExpiryDays: 7, maxQuantity: 5 },
    attendance: { notifyGuardiansOnAbsence: true, lateAfterMinutes: 10 },
    reports: { weeklyReportEnabled: true, weeklyReportDay: 'sun', weeklyReportTime: '19:00' },
    assignments: { autoApplyBundlesToNewStudents: false },
    teacher: { scope: 'related' } };
  routes['GET /v1/admin/settings'] = () => settings;
  routes['PATCH /v1/admin/settings'] = (body) => { settings = { ...settings, ...body }; return settings; };
  routes['GET /v1/admin/features'] = { items: [
    { key: 'teacher.portal', enabled: false, config: null, editable: true },
  ] };
  const { app, root, toasts } = await makeApp(load, document);
  const { section } = await load('./panels/bbd_akademi/settings.js');
  const panel = section(app);
  root.append(panel.node);
  panel.shown({ tab: 'features' });
  await sleep(40);
  const out = {};
  out.featureText = panel.node.textContent.includes('Bugün (dersler, yoklama), Öğrencilerim');
  out.placeholderGone = !panel.node.textContent.includes('yer tutucu');
  panel.shown({ tab: 'institution' });
  await sleep(60);
  const cards = root.querySelectorAll('.kit-card');
  const box = cards.find((node) => node.textContent.includes('Öğretmen kapsamı'));
  out.cardFound = Boolean(box);
  const select = box.querySelector('select');
  out.options = select.querySelectorAll('option').map((o) => [o.value, o.textContent]);
  out.initial = select.value;
  out.a11y = audit(box);
  select.value = 'all';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(20);
  click(byText(root, 'button', 'Kaydet'));
  await sleep(60);
  out.patches = sent.filter((c) => c.method === 'PATCH' && c.path === '/v1/admin/settings').map((c) => c.body);
  out.after = root.querySelectorAll('.kit-card').find((node) => node.textContent.includes('Öğretmen kapsamı'))
    .querySelector('select').value;
  out.toasts = toasts.map(([text, tone]) => [text, tone]);
  return out;
}
"""


def test_kurum_ayari_ogretmen_kapsami_related_all() -> None:
    out: dict[str, Any] = node_run(SCOPE)
    assert out["featureText"] and out["placeholderGone"]
    assert out["cardFound"]
    assert out["options"] == [
        ["related", "Yalnız ders verdiği ve koçu olduğu öğrenciler"],
        ["all", "Kurumdaki bütün öğrenciler"],
    ]
    assert out["initial"] == "related"
    assert out["a11y"] == []
    # Yalnız değişen grup gider (kısmi güncelleme): başka ayar ezilmez.
    assert out["patches"] == [{"teacher": {"scope": "all"}}]
    assert out["after"] == "all"
    assert ["Kurum ayarları kaydedildi.", "good"] in out["toasts"]


def test_personel_ipucu_ogretmen_kapsamina_yonlendirir() -> None:
    staff_js = (PANEL / "staff.js").read_text(encoding="utf-8")
    assert "Kurum → Öğretmen / koç" in staff_js
    assert "yönetici de öğretmen" in staff_js
    settings_js = (PANEL / "settings.js").read_text(encoding="utf-8")
    assert "path: ['teacher', 'scope']" in settings_js
    assert "yer tutucu ekran" not in settings_js
