"""Koçluk şablonları (Akademi README §14 madde 115) — KM paneli.

Kullanıcı (16.10.2026): "koçluk kısmına şablon ekleyelim".

- `text-templates-logic.js` (saf, Node): süzgeç / arama (Türkçe harf duyarsız), forma yerleştirme (boş alan → metin,
  dolu alan → altına ekle, sınırda kes), eylem maddeleri, gövde doğrulaması, kaydetme kapsamı, haftadan şablon;
- vekil: yeni uçlar izin listesinde, KM izinleri (liste görüntüleme, kurum şablonu koçluk yönetimi, doldurma koç
  adına yazma);
- DOM: Koçluk → Şablonlar sekmesi (kurum şablonu ekle), tebrik penceresinde "Şablondan doldur" → sunucuda dolu metin
  forma gelir → gönderilir; "Şablon olarak kaydet" kurum şablonu yazar; haftalık programda "Bu haftayı şablon olarak
  kaydet" kurum plan şablonu açar.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import (
    LEARNING,
    LEARNING_SETUP,
    VIEW,
    km_permissions,
)
from modules.bbd_akademi.tests.test_akademi_kocluk import PRELUDE, node_run

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

LOGIC = r"""
import * as m from './logic.mjs';
const items = [
  { id: 'a', kind: 'kudos', title: 'İstikrar', body: '{ogrenci_adi}, günlerdir çalışıyorsun', active: true },
  { id: 'b', kind: 'kudos', title: 'Pasif', body: 'x', active: false },
  { id: 'c', kind: 'meeting_note', title: 'Deneme sonrası', body: 'Netler', active: true },
];
const out = {
  filter: [m.filterTemplates(items, { kind: 'kudos' }).map((x) => x.id),
    m.filterTemplates(items, { q: 'ISTIKRAR çalış' }).map((x) => x.id),
    m.filterTemplates(items, { kind: 'kudos', includeInactive: true }).map((x) => x.id)],
  fold: m.fold('  İSTİKRAR  Çalışma '),
  merge: [m.mergeText('', 'Tebrikler Elif!', 280), m.mergeText('Not var', 'Ek', 4000), m.mergeText('abc', 'defgh', 7)],
  actions: [m.parseActions('✓ Bir\n\n İki \nÜç'), m.actionsText(['A', { text: 'B' }]), m.mergeActions(['A'], ['A', 'B'])],
  inline: m.withInlineActions('Not', ['Ara', ' ']),
  unfilled: m.unfilledNote(['ders', 'veli_adi']),
  body: [m.templateBody({ title: 'Deneme notu', body: ' Metin ', actionItems: 'Bir\nİki', noteVisibility: 'student' },
    { kind: 'meeting_note', scope: 'institution' }),
    m.templateBody({ title: 'T', body: 'x' }, { kind: 'kudos' }).error,
    m.templateBody({ title: 'Tebrik', body: 'x'.repeat(281) }, { kind: 'kudos' }).error,
    m.templateBody({ title: 'Tebrik', body: 'x', actionItems: 'madde' }, { kind: 'kudos' }).error,
    m.templateBody({ active: false }, { kind: 'kudos', partial: true })],
  scope: [m.saveScope({ canCurate: true, canCreatePersonal: false }), m.saveScope({ canCurate: false, canCreatePersonal: true }),
    m.saveScope({})],
  week: [m.weekTemplateBody({ tasks: [], durationDays: 7 }, 'Ad').error,
    m.weekTemplateBody({ tasks: [{ dayOffset: 0 }], durationDays: 7 }, ' Elif haftası ')],
  start: [m.templateStart('2026-10-12', '2026-10-16'), m.templateStart('2026-10-19', '2026-10-16')],
  preview: m.preview('\n  İlk satır burada \nikinci', 9),
};
process.stdout.write(JSON.stringify(out));
"""


def test_saf_mantik(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (tmp_path / "logic.mjs").write_bytes((PANEL / "shared/text-templates-logic.js").read_bytes())
    (tmp_path / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert out["filter"] == [["a"], ["a"], ["a", "b"]]
    assert out["fold"] == "istikrar calisma"
    assert out["merge"] == [{"text": "Tebrikler Elif!", "truncated": False}, {"text": "Not var\n\nEk", "truncated": False},
                            {"text": "abc\n\nde", "truncated": True}]
    assert out["actions"] == [["Bir", "İki", "Üç"], "A\nB", ["A", "B"]]
    assert out["inline"] == "Not\n\nYapılacaklar:\n- Ara"
    assert out["unfilled"].startswith("Doldurulamayan yer tutucu: Ders, Velinin adı soyadı")
    body, short, long, actions, partial = out["body"]
    assert body == {"body": {"scope": "institution", "kind": "meeting_note", "title": "Deneme notu", "body": "Metin",
                             "actionItems": ["Bir", "İki"], "noteVisibility": "student"}}
    assert short == "Başlık en az 2 karakter olmalı."
    assert long == "Tebrik için metin en çok 280 karakter olabilir."
    assert actions.startswith("Eylem maddesi yalnız")
    assert partial == {"body": {"active": False}}
    assert out["scope"] == ["institution", "personal", None]
    assert out["week"][0] == "Bu haftada şablona alınacak koç görevi yok."
    assert out["week"][1] == {"body": {"name": "Elif haftası", "description": None, "durationDays": 7,
                                       "tasks": [{"dayOffset": 0}]}}
    assert out["start"] == ["2026-10-16", "2026-10-19"]
    assert out["preview"] == "İlk satı…"


def test_vekil_izinleri() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    expected = {
        "admin_listCoachTextTemplates": (VIEW,),
        "admin_createCoachTextTemplate": (LEARNING_SETUP,),
        "admin_updateCoachTextTemplate": (LEARNING_SETUP,),
        "admin_deleteCoachTextTemplate": (LEARNING_SETUP,),
        "admin_renderCoachTextTemplate": (LEARNING,),
        "admin_getCoachWeekTemplateDraft": (LEARNING,),
    }
    for op_id, permissions in expected.items():
        assert op_id in ops, op_id
        assert km_permissions(ops[op_id]) == permissions, op_id
        assert ops[op_id].surface == "both", op_id
    assert ops["admin_getCoachWeekTemplateDraft"].feature_flag == "coaching"


UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  const student = { id: 'stu-1', externalRef: 'STU-1', displayName: 'Elif Kaya', firstName: 'Elif', lastName: 'Kaya',
    classGroup: { id: 'c1', name: '9-A' }, status: 'active' };
  const tpl = (extra) => ({ scope: 'built_in', kind: 'kudos', actionItems: [], noteVisibility: null, placeholders: ['ogrenci_adi'],
    sort: 500, active: true, usageCount: 0, lastUsedAt: null, owner: null, canEdit: true, updatedAt: null, ...extra });
  const list = { canCurate: true, canCreatePersonal: false, placeholders: [], maxLengths: { kudos: 280, meeting_note: 4000 },
    items: [tpl({ id: 'builtin.kudos.week_done', title: 'Programı tamamladı', body: 'Tebrikler {ogrenci_adi}! Programını tamamladın.' }),
      tpl({ id: 'builtin.kudos.streak', title: 'İstikrar', body: '{ogrenci_adi}, günlerdir aksatmadan çalışıyorsun.', usageCount: 4 }),
      tpl({ id: 'i1', scope: 'institution', kind: 'meeting_note', title: 'Kurum notu', body: 'Görüşme {tarih}',
        actionItems: ['Hata defteri'], usageCount: 2, active: false })] };
  routes['GET /v1/admin/coaching/text-templates'] = (body, payload) => ({ ...list,
    items: list.items.filter((item) => !payload.query?.kind || item.kind === payload.query.kind) });
  routes['POST /v1/admin/coaching/text-templates/builtin\\.kudos\\.streak/render'] = (body) => ({ templateId: 'builtin.kudos.streak',
    kind: 'kudos', title: 'İstikrar', text: 'Elif, günlerdir aksatmadan çalışıyorsun.', actionItems: [], noteVisibility: null,
    filled: ['ogrenci_adi'], unfilled: [], maxLength: 280, truncated: false, echo: body });
  routes['POST /v1/admin/coaching/text-templates'] = (body) => tpl({ id: 'new', ...body, scope: body.scope || 'personal' });
  routes['GET /v1/admin/coaching/badges'] = { items: [] };
  routes['POST /v1/admin/students/stu-1/kudos'] = (body) => ({ id: 'k1', ...body });
  routes['GET /v1/admin/students/stu-1/coach-week/template-draft'] = { name: 'Elif — 12.10–18.10 haftası', description: null,
    durationDays: 7, skippedCount: 1, weekStart: '2026-10-12',
    tasks: [{ dayOffset: 0, estimatedMinutes: 40, type: 'free', title: 'Formül tekrarı', sectionIds: [] }] };
  routes['POST /v1/admin/plan/templates'] = (body) => ({ id: 'pt1', version: 1, appliedCount: 0, ...body });

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const toast = toaster(root);
  const app = { root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { classGroups: async () => [], studentGroups: async () => [], terms: async () => [], subjects: async () => [],
      async options() { return []; } } };
  const out = {};

  // Koçluk → Şablonlar sekmesi.
  const { section } = await load('./panels/bbd_akademi/coaching/coaching.js');
  const view = section(app);
  root.append(view.node);
  view.shown({ coachingTab: 'templates' });
  await sleep(120);
  out.tabs = root.querySelectorAll('.kit-tabs [role="tab"]').map((tab) => tab.textContent.trim());
  const pane = root.querySelectorAll('.ak-coach-pane').find((item) => !item.hidden);
  out.paneText = pane?.textContent || '';
  out.listQuery = sent.find((c) => c.path === '/v1/admin/coaching/text-templates')?.query;
  out.a11yTab = audit(pane);
  click(byText(pane, 'button', 'Kurum şablonu ekle'));
  await sleep(40);
  let dialog = root.querySelectorAll('.kit-dialog').pop();
  out.a11yEdit = audit(dialog);
  choose(dialog.querySelector('select'), 'kudos');
  type(dialog.querySelectorAll('input')[0], 'Deneme tebriği');
  type(dialog.querySelectorAll('textarea')[0], 'Tebrikler {ogrenci_adi}, netlerin arttı!');
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.created = sent.filter((c) => c.path === '/v1/admin/coaching/text-templates' && c.method === 'POST').map((c) => c.body);

  // Tebrik penceresi: şablondan doldur → gönder; sonra şablon olarak kaydet.
  const { kudosDialog } = await load('./panels/bbd_akademi/coaching/coaching-students.js');
  const pending = kudosDialog(app, student);
  await sleep(60);
  dialog = root.querySelectorAll('.kit-dialog').pop();
  out.a11yKudos = audit(dialog);
  const text = () => dialog.querySelector('textarea');
  type(text(), '');
  click(byText(dialog, 'button', 'Şablondan doldur'));
  await sleep(60);
  out.pickerText = dialog.querySelector('.ak-tt-panel')?.textContent || '';
  out.a11yPicker = audit(dialog);
  type(dialog.querySelector('.ak-tt-search'), 'istikrar');
  await sleep(20);
  out.pickerItems = dialog.querySelectorAll('.ak-tt-item').map((item) => item.textContent);
  click(dialog.querySelectorAll('.ak-tt-item')[0]);
  await sleep(60);
  out.render = sent.filter((c) => c.path.endsWith('/render')).map((c) => ({ path: c.path, body: c.body }));
  out.filledText = text().value;
  out.status = dialog.querySelector('.ak-tt-status')?.textContent || '';
  click(byText(dialog, 'button', 'Şablon olarak kaydet'));
  await sleep(40);
  type(dialog.querySelector('.ak-tt-save input'), 'Elif istikrar');
  click(byText(dialog.querySelector('.ak-tt-save'), 'button', 'Kaydet'));
  await sleep(60);
  out.savedFromForm = sent.filter((c) => c.path === '/v1/admin/coaching/text-templates' && c.method === 'POST').map((c) => c.body).pop();
  click(byText(dialog, 'button', 'Gönder'));
  await sleep(60);
  await pending;
  out.kudos = sent.filter((c) => c.path === '/v1/admin/students/stu-1/kudos').map((c) => c.body);

  // Haftalık program → Bu haftayı şablon olarak kaydet.
  const { saveWeekTemplate } = await load('./panels/bbd_akademi/coaching/coaching-week.js');
  const saving = saveWeekTemplate(app, student, '2026-10-14');
  await sleep(60);
  dialog = root.querySelectorAll('.kit-dialog').pop();
  out.weekDialog = dialog.textContent;
  click(byText(dialog, 'button', 'Şablonu kaydet'));
  await saving;
  out.draftQuery = sent.find((c) => c.path.endsWith('/template-draft'))?.query;
  out.planTemplate = sent.filter((c) => c.path === '/v1/admin/plan/templates' && c.method === 'POST').map((c) => c.body);
  out.toasts = toasts.map(([text]) => text);
  return out;
}
"""


def test_sablonlar_sekmesi_secici_ve_haftadan_sablon() -> None:
    out = node_run(UI)
    assert "Şablonlar" in out["tabs"] and "Tebrik ve check-in" in out["tabs"] and "Check-in" not in out["tabs"]
    assert "Programı tamamladı" in out["paneText"] and "Kurum notu" in out["paneText"] and "Pasif" in out["paneText"]
    assert out["listQuery"] == {"includeInactive": "true"}
    assert out["a11yTab"] == [] and out["a11yEdit"] == []
    (created,) = out["created"]
    assert created["scope"] == "institution" and created["kind"] == "kudos"
    assert created["body"] == "Tebrikler {ogrenci_adi}, netlerin arttı!" and created["title"] == "Deneme tebriği"
    assert out["a11yKudos"] == [] and out["a11yPicker"] == []
    assert "Programı tamamladı" in out["pickerText"] and "Kurum notu" not in out["pickerText"]   # türe göre süzülü
    assert len(out["pickerItems"]) == 1 and "İstikrar" in out["pickerItems"][0]
    (render,) = out["render"]
    assert render["body"] == {"studentId": "stu-1"}
    assert out["filledText"] == "Elif, günlerdir aksatmadan çalışıyorsun."
    assert out["status"].startswith("Şablon forma yerleşti")
    assert out["savedFromForm"] == {"scope": "institution", "kind": "kudos", "title": "Elif istikrar",
                                    "body": "Elif, günlerdir aksatmadan çalışıyorsun."}
    assert out["kudos"] == [{"text": "Elif, günlerdir aksatmadan çalışıyorsun."}]
    assert "1 koç görevi şablona alınır" in out["weekDialog"] and "1 görev atlandı" in out["weekDialog"]
    assert out["draftQuery"] == {"week": "2026-10-14"}
    (plan,) = out["planTemplate"]
    assert plan["name"] == "Elif — 12.10–18.10 haftası" and plan["durationDays"] == 7 and len(plan["tasks"]) == 1
    assert any("şablonu kaydedildi" in text for text in out["toasts"])
