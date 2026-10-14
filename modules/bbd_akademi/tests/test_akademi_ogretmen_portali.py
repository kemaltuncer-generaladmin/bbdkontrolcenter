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


# --------------------------------------------------------------------------- öğrenci adına kayıt etiketi
# Kullanıcı: "Onları da yapabilsin." — yönetici öğrenci / veli ADINA yazar; kayıt `actedBy` "Yönetici tarafından"
# taşır (Akademi README §14 madde 65). KM öğrenci kartı listelerinde etiket görünür.

ACTED_BY = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const log = (id, actedBy) => ({ id, clientUuid: id, mode: 'manual', source: 'quick_entry', planTaskId: null,
    startedAt: '2026-10-14T08:00:00Z', endedAt: '2026-10-14T08:30:00Z', durationSeconds: 1800,
    subject: { id: 'matematik', name: 'Matematik' }, topic: null, resourceId: null, sectionId: null,
    correct: 7, wrong: 1, blank: 0, note: null, createdAt: '2026-10-14T08:30:00Z', updatedAt: null, actedBy });
  routes['GET /v1/students/stu-1/study-logs'] = { items: [log('l-1', 'Yönetici tarafından'), log('l-2', null)],
    nextCursor: null };
  const { app, root } = await makeApp(load, document);
  app.can = () => false;  // tablet kartı yok
  const { studyTab } = await load('./panels/bbd_akademi/students-records.js');
  const ctx = { app, id: 'stu-1', dispose: () => {}, subjectNames: new Map() };
  const tab = studyTab(ctx);
  root.append(tab.node);
  await sleep(60);
  const rows = root.querySelectorAll('.kit-row');
  return {
    rows: rows.length,
    labelled: rows.filter((row) => row.textContent.includes('Yönetici tarafından')).length,
    a11y: audit(tab.node),
  };
}
"""


def test_ogrenci_adina_kayit_etiketi_listede() -> None:
    out: dict[str, Any] = node_run(ACTED_BY)
    assert out["rows"] >= 2
    assert out["labelled"] == 1
    assert out["a11y"] == []
    learning = (PANEL / "students-learning.js").read_text(encoding="utf-8")
    assert "row.actedBy" in learning and "mistake.actedBy" in learning  # hata defteri listesi + çekmece
    records = (PANEL / "students-records.js").read_text(encoding="utf-8")
    assert records.count("row.actedBy") == 2  # dış deneme "Giren" + çalışma kaydı "Kayıt"
