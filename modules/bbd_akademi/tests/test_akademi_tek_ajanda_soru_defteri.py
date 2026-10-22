"""BBD Akademi 74 — öğrenci "Öğrenme": tek Ajanda + Soru defteri (salt okuma) + Ayarlar bayrağı (Node'da davranış).

Kullanıcı (22.10.2026): "plan ve ajanda farkını kaldıralım… hem koç görevleri hem ders programı hem de öğrencinin
kendi ekledikleri görülsün ve kontrol merkezinde de öğrencinin kendi görevleri görülebilsin" (Akademi README §14
madde 133) ve "öğrenciye hata defterine ek olarak kendi soru defterleri olsun… ders seçsin sonra klasör oluşturur
gibi bölüm oluştursun" (madde 136; kurum salt okur).

- Öğrenme iç sekmeleri: "Çalışma planı" kalktı, ilk sekme "Ajanda"; eski `learningTab: 'plan'` Ajanda'yı açar;
  "Soru defteri" hata defterinin yanında (bayrak kapalıysa `app.can` false → yok).
- Ajanda haftası tek akış: ders ("Ders"), koç görevi ("Koç: Ad", Yapıldı / erteleme talebi), öğrencinin kendi
  görevi / kişisel etkinliği / yapılacağı ("Öğrencinin kendi eklediği"); özet kutuları (uyum + koç görevi + kendi
  öğeleri + erteleme); kaynak süzgeci istemcide; istekler haftalık (`from`/`to`) ve uyum 8 hafta.
- Soru defteri: ders kartı → bölüm (klasör) → küçük resim ızgarası → büyük görüntü (not, Çözüldü); istek
  parametreleri (`subjectId`, `sectionId`, `unsectioned`, `solved`).
- Ayarlar → Özellikler'de `question_notebook` "Soru defteri" adıyla diğer bayraklarla aynı listede.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

APP = r"""
  const gets = [];
  const routes = {};
  const app = {
    root: document.body,
    can: (id) => !(globalThis.__deny || []).includes(id),
    toast: () => {},
    api: {
      can: (id) => !(globalThis.__deny || []).includes(id),
      featureOff: () => false,
      invalidate: () => {},
      getCached: async (path, query) => app.api.get(path, query),
      get: async (path, query = {}) => {
        gets.push({ path, query: JSON.parse(JSON.stringify(query || {})) });
        for (const [key, value] of Object.entries(routes)) {
          if (path.endsWith(key)) return typeof value === 'function' ? value(query || {}) : JSON.parse(JSON.stringify(value));
        }
        return { items: [], nextCursor: null };
      },
      pages: (path, query = {}, { limit = 50 } = {}) => {
        let done = false;
        return {
          get done() { return done; },
          async next() { done = true; return (await app.api.get(path, { ...query, limit })).items; },
        };
      },
      fetchFile: async () => ({ filename: 'x.jpg', blob: new Blob(['x'], { type: 'image/jpeg' }) }),
    },
  };
"""

AGENDA = r"""
export default async function ({ document, audit, sleep, load }) {
""" + APP + r"""
  const { todayIst, weekStart, addDays } = await load('./panels/bbd_akademi/common.js');
  const week = weekStart(todayIst());
  const d = (n) => addDays(week, n);
  const at = (n, clock) => `${d(n)}T${clock}:00+03:00`;
  const base = { subtitle: null, endDate: null, allDay: false, location: null, subject: null, recurring: false,
    reminderMinutes: null, readOnly: true, institutionEventKind: null, deepLink: '', classSessionId: null,
    recordings: null, planTask: null, ownerName: null, createdByStudent: false, isPrivate: false };
  const planTask = (over) => ({ taskId: 'T', taskType: 'question_solving', source: 'coach', locked: true,
    estimatedMinutes: 40, completed: false, coachName: 'Merve Koç', postponeRequestStatus: null,
    postponeRequestedDate: null, canComplete: false, canUncomplete: false, canMove: false, canRequestPostpone: false,
    canCancelPostpone: false, canEdit: false, canDelete: false, ...over });
  const ITEMS = [
    { ...base, id: 'lesson:L1', type: 'lesson', title: 'Matematik', date: d(0), startsAt: at(0, '09:00'),
      endsAt: at(0, '09:40'), status: 'scheduled', source: { kind: 'schedule_item', id: 'L1' }, origin: 'institution',
      classSessionId: '0192b7a4-2b3c-7d4e-8f50-6a7b8c9d0e99' },
    { ...base, id: 'plan_task:T1', type: 'plan_task', title: 'Türev 40 soru', date: d(0), startsAt: null, endsAt: null,
      allDay: true, status: 'done', source: { kind: 'plan_task', id: 'T1' }, origin: 'coach', ownerName: 'Merve Koç',
      planTask: planTask({ taskId: 'T1', completed: true }) },
    { ...base, id: 'plan_task:T2', type: 'plan_task', title: 'Paragraf 20 soru', date: d(1), startsAt: null,
      endsAt: null, allDay: true, status: 'planned', source: { kind: 'plan_task', id: 'T2' }, origin: 'coach',
      ownerName: 'Merve Koç', planTask: planTask({ taskId: 'T2', postponeRequestStatus: 'requested',
        postponeRequestedDate: d(3) }) },
    { ...base, id: 'plan_task:T3', type: 'plan_task', title: 'Kendi tekrarım', date: d(1), startsAt: null, endsAt: null,
      allDay: true, status: 'planned', source: { kind: 'plan_task', id: 'T3' }, origin: 'self', createdByStudent: true,
      planTask: planTask({ taskId: 'T3', source: 'self', locked: false, coachName: null }) },
    { ...base, id: 'personal_event:E1:x', type: 'personal_event', title: 'Piyano dersi', date: d(2),
      startsAt: at(2, '18:00'), endsAt: at(2, '19:00'), status: null, source: { kind: 'agenda_event', id: 'E1' },
      origin: 'self', createdByStudent: true, isPrivate: true },
    { ...base, id: 'todo:D1', type: 'todo', title: 'Kitap iade', date: d(2), startsAt: null, endsAt: null, allDay: true,
      status: 'open', source: { kind: 'agenda_todo', id: 'D1' }, origin: 'self', createdByStudent: true, isPrivate: true },
  ];
  routes['/agenda'] = () => ({ items: ITEMS, holidays: [], nextCursor: null });
  routes['/plan/adherence'] = { computedAt: at(0, '03:50'), averagePercent: 70, trend: 'up', weeks: [
    { weekStart: week, inProgress: true, plannedTasks: 3, doneTasks: 1, partialTasks: 0, skippedTasks: 0,
      movedTasks: 0, plannedMinutes: 120, doneMinutes: 40, adherencePercent: 33, bySubject: [] }] };
  routes['/agenda/countdown'] = { examTarget: null, nextInstitutionExam: null };
  routes['/plan/preferences'] = { availability: [], notifications: {}, restDays: [] };
  routes['/plan/tasks/T2'] = { id: 'T2', title: 'Paragraf 20 soru', type: 'question_solving', status: 'planned',
    date: d(1), source: 'coach', locked: true, estimatedMinutes: 40, createdAt: at(0, '08:00'),
    updatedAt: at(0, '08:00'), postponeRequest: { status: 'requested', requestedDate: d(3), reason: 'Sınav',
      createdAt: at(0, '10:00') } };

  const { learningPanel } = await load('./panels/bbd_akademi/students/students-learning.js');
  const root = document.createElement('div');
  document.body.append(root);
  const panel = learningPanel(app, 'S1');
  root.append(panel.node);
  panel.shown({ learningTab: 'plan' });
  await sleep(60);
  const out = {};
  out.tabs = root.querySelectorAll('[role="tab"]').map((tab) => tab.textContent);
  out.selected = root.querySelectorAll('[role="tab"]').find((tab) => tab.getAttribute('aria-selected') === 'true')?.textContent;
  out.agendaQuery = gets.find((g) => g.path === '/v1/students/S1/agenda')?.query;
  out.adherenceQuery = gets.find((g) => g.path === '/v1/students/S1/plan/adherence')?.query;
  out.week = week;
  out.weekMinus49 = addDays(week, -49);
  out.weekEnd = addDays(week, 6);
  const rows = root.querySelectorAll('.ak-sl-agenda-row');
  out.rows = rows.map((row) => row.textContent);
  out.days = root.querySelectorAll('.ak-sl-agenda-day').length;
  out.kpi = root.querySelectorAll('.kit-kpi-tile').map((tile) => tile.textContent);
  out.chips = root.querySelectorAll('.kit-chips .kit-chip').map((chip) => chip.textContent);
  out.violations = audit(root);
  // kaynak süzgeci: "Öğrencinin kendi eklediği"
  root.querySelectorAll('.kit-chips .kit-chip').find((chip) => chip.textContent.startsWith('Öğrencinin kendi eklediği')).click();
  await sleep(10);
  out.selfRows = root.querySelectorAll('.ak-sl-agenda-row').map((row) => row.textContent);
  out.agendaCalls = gets.filter((g) => g.path === '/v1/students/S1/agenda').length;
  // tür süzgeci: Yapılacak kapatılınca istek türleri daralır
  root.querySelectorAll('.ak-chips .kit-chip').find((chip) => chip.textContent === 'Yapılacak').click();
  await sleep(20);
  out.typesQuery = gets.filter((g) => g.path === '/v1/students/S1/agenda').pop().query.types;
  // koç görevi → çekmece (salt okunur)
  root.querySelectorAll('.kit-chips .kit-chip').find((chip) => chip.textContent.startsWith('Öğrencinin kendi eklediği')).click();
  await sleep(10);
  root.querySelectorAll('button.ak-sl-agenda-row').find((row) => row.textContent.includes('Paragraf')).click();
  await sleep(30);
  out.drawer = document.body.textContent.includes('Erteleme talebi bekliyor');
  return out;
}
"""

NOTEBOOK = r"""
export default async function ({ document, audit, sleep, load }) {
""" + APP + r"""
  const file = (id) => ({ id, kind: 'notebook_question_image', contentType: 'image/jpeg', width: 800, height: 600,
    url: `https://api.example/files/${id}`, urlExpiresAt: new Date(Date.now() + 300000).toISOString(),
    thumbnailUrl: `https://api.example/files/${id}.thumb.jpg`, hidden: false });
  const MAT = { id: 'matematik', name: 'Matematik', shortName: 'MAT' };
  routes['/question-notebook'] = { itemCount: 3, solvedCount: 1, subjects: [
    { subject: MAT, itemCount: 3, solvedCount: 1, sectionCount: 1, lastAddedAt: '2026-10-22T19:40:00+03:00',
      lastThumbnail: file('F1'), inCurriculum: true },
    { subject: { id: 'fizik', name: 'Fizik', shortName: 'FİZ' }, itemCount: 0, solvedCount: 0, sectionCount: 0,
      lastAddedAt: null, lastThumbnail: null, inCurriculum: true }] };
  routes['/question-notebook/sections'] = { subject: MAT, unsectionedCount: 1, unsectionedSolvedCount: 0, items: [
    { id: 'SEC1', subjectId: 'matematik', name: 'Türev', sortOrder: 0, itemCount: 2, solvedCount: 1,
      coverThumbnail: file('F1'), createdAt: '2026-10-20T18:00:00+03:00', updatedAt: '2026-10-22T19:40:00+03:00' }] };
  const item = (id, solved, note) => ({ id, clientUuid: id, subject: MAT, sectionId: 'SEC1', note, solved,
    solvedAt: solved ? '2026-10-22T20:00:00+03:00' : null, images: [file(`${id}-a`), file(`${id}-b`)],
    createdAt: '2026-10-22T19:40:00+03:00', updatedAt: '2026-10-22T19:40:00+03:00' });
  routes['/question-notebook/items'] = { items: [item('I1', true, 'Zincir kuralı — tekrar çöz.'), item('I2', false, null)],
    nextCursor: null };
  routes['/question-notebook/items/I1'] = item('I1', true, 'Zincir kuralı — tekrar çöz.');

  const { learningPanel } = await load('./panels/bbd_akademi/students/students-learning.js');
  const root = document.createElement('div');
  document.body.append(root);
  const panel = learningPanel(app, 'S1');
  root.append(panel.node);
  panel.shown({ learningTab: 'notebook' });
  await sleep(40);
  const out = {};
  out.cards = root.querySelectorAll('.ak-sl-nb-card').map((card) => card.textContent);
  out.kpi = root.querySelectorAll('.ak-sl-nb .kit-kpi-tile').map((tile) => tile.textContent);
  out.violations1 = audit(root);
  root.querySelectorAll('.ak-sl-nb-card').find((card) => card.textContent.includes('Matematik')).click();
  await sleep(40);
  out.folders = root.querySelectorAll('.ak-sl-nb-card').map((card) => card.textContent);
  out.sectionsQuery = gets.filter((g) => g.path.endsWith('/question-notebook/sections')).pop().query;
  root.querySelectorAll('.ak-sl-nb-card').find((card) => card.textContent.startsWith('Türev')).click();
  await sleep(40);
  out.crumbs = root.querySelector('.ak-sl-nb-crumbs').textContent;
  out.itemsQuery = gets.filter((g) => g.path.endsWith('/question-notebook/items')).pop().query;
  out.tiles = root.querySelectorAll('.ak-sl-nb-tile').map((tile) => tile.textContent);
  out.images = root.querySelectorAll('.ak-sl-nb-tile img').length;
  out.violations2 = audit(root);
  // Çözüldü süzgeci
  root.querySelectorAll('.kit-chips .kit-chip').find((chip) => chip.textContent === 'Çözüldü').click();
  await sleep(30);
  out.solvedQuery = gets.filter((g) => g.path.endsWith('/question-notebook/items')).pop().query;
  root.querySelectorAll('.kit-chips .kit-chip').find((chip) => chip.textContent === 'Çözüldü').click();
  await sleep(30);
  // büyük görüntü
  root.querySelectorAll('.ak-sl-nb-tile')[0].click();
  await sleep(40);
  out.itemFetched = gets.some((g) => g.path.endsWith('/question-notebook/items/I1'));
  const drawer = document.body.querySelectorAll('[role="dialog"]').pop();
  out.drawer = drawer ? drawer.textContent : '';
  out.drawerImages = drawer ? drawer.querySelectorAll('img').length : 0;
  // Bölümsüz
  root.querySelectorAll('.ak-sl-nb-crumbs button').find((b) => b.textContent === 'Matematik').click();
  await sleep(40);
  root.querySelectorAll('.ak-sl-nb-card').find((card) => card.textContent.startsWith('Bölümsüz')).click();
  await sleep(40);
  out.unsectionedQuery = gets.filter((g) => g.path.endsWith('/question-notebook/items')).pop().query;
  return out;
}
"""

FLAG_OFF = r"""
export default async function ({ document, sleep, load }) {
""" + APP + r"""
  globalThis.__deny = ['students_getQuestionNotebook'];
  const { learningPanel } = await load('./panels/bbd_akademi/students/students-learning.js');
  const panel = learningPanel(app, 'S1');
  document.body.append(panel.node);
  panel.shown();
  await sleep(20);
  return { tabs: panel.node.querySelectorAll('[role="tab"]').map((tab) => tab.textContent) };
}
"""

SETTINGS = r"""
export default async function ({ document, sleep, load }) {
""" + APP + r"""
  routes['/v1/admin/features'] = { items: [
    { key: 'guardian_mistake_images', enabled: true, editable: true, config: null },
    { key: 'question_notebook', enabled: true, editable: true, config: null },
    { key: 'coaching', enabled: true, editable: true, config: null }] };
  const { section } = await load('./panels/bbd_akademi/admin/settings.js');
  const view = section(app);
  document.body.append(view.node);
  view.shown({ tab: 'features' });
  await sleep(40);
  return { rows: view.node.querySelectorAll('.ak-set-flag').map((row) => row.textContent) };
}
"""


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


@pytest.fixture(scope="module")
def agenda() -> dict[str, Any]:
    return _run(AGENDA)


@pytest.fixture(scope="module")
def notebook() -> dict[str, Any]:
    return _run(NOTEBOOK)


def test_ogrenme_sekmeleri_tek_ajanda_ve_soru_defteri(agenda: dict[str, Any]) -> None:
    assert agenda["tabs"][0] == "Ajanda"
    assert "Çalışma planı" not in agenda["tabs"]
    assert agenda["tabs"][:3] == ["Ajanda", "Hata defteri", "Soru defteri"]
    # eski derin bağlantı (learningTab: 'plan') Ajanda'yı açar
    assert agenda["selected"] == "Ajanda"


def test_ajanda_haftalik_istekler(agenda: dict[str, Any]) -> None:
    assert agenda["agendaQuery"]["from"] == agenda["week"]
    assert agenda["agendaQuery"]["to"] == agenda["weekEnd"]
    assert "types" not in agenda["agendaQuery"]          # bütün türler (kendi öğeleri dahil)
    assert agenda["adherenceQuery"] == {"from": agenda["weekMinus49"], "to": agenda["weekEnd"]}
    assert agenda["days"] == 7


def test_ajanda_kaynak_rozetleri(agenda: dict[str, Any]) -> None:
    rows = agenda["rows"]
    lesson = next(row for row in rows if "Matematik" in row)
    assert "Ders" in lesson
    done = next(row for row in rows if "Türev 40 soru" in row)
    assert "Koç: Merve Koç" in done and "Yapıldı" in done
    postponed = next(row for row in rows if "Paragraf" in row)
    assert "Koç: Merve Koç" in postponed and "Erteleme talebi" in postponed
    for title in ("Kendi tekrarım", "Piyano dersi", "Kitap iade"):
        row = next(row for row in rows if title in row)
        assert "Öğrencinin kendi eklediği" in row, row
    assert "Açık" in next(row for row in rows if "Kitap iade" in row)


def test_ajanda_ozet_ve_suzgec(agenda: dict[str, Any]) -> None:
    kpi = " | ".join(agenda["kpi"])
    assert "Plan uyumu (hafta sürüyor)" in kpi and "33" in kpi
    assert "Koç görevi (tamamlanan)1 / 2" in kpi.replace("\n", "")
    assert "Öğrencinin kendi eklediği3" in kpi.replace("\n", "")
    assert "Erteleme talebi1" in kpi.replace("\n", "")
    assert any(chip.startswith("Öğrencinin kendi eklediği") and chip.endswith("3") for chip in agenda["chips"])
    assert all("Öğrencinin kendi eklediği" in row for row in agenda["selfRows"])
    assert len(agenda["selfRows"]) == 3
    assert agenda["agendaCalls"] == 1, "kaynak süzgeci istemcide — yeni istek yok"
    assert "todo" not in agenda["typesQuery"] and "personal_event" in agenda["typesQuery"]
    assert agenda["drawer"] is True


def test_ajanda_erisilebilir(agenda: dict[str, Any]) -> None:
    assert agenda["violations"] == []


def test_soru_defteri_ders_bolum_soru(notebook: dict[str, Any]) -> None:
    # içeriksiz ders kartı çizilmez
    assert len(notebook["cards"]) == 1 and "Matematik" in notebook["cards"][0]
    assert "3 soru · 1 çözüldü" in notebook["cards"][0] and "1 bölüm" in notebook["cards"][0]
    assert any("Toplam soru" in tile and "3" in tile for tile in notebook["kpi"])
    assert notebook["sectionsQuery"] == {"subjectId": "matematik"}
    folders = notebook["folders"]
    assert folders[0].startswith("Tüm sorular") and "3 soru" in folders[0]
    assert any(folder.startswith("Türev") and "2 soru · 1 çözüldü" in folder for folder in folders)
    assert any(folder.startswith("Bölümsüz") and "1 soru" in folder for folder in folders)
    assert "Dersler" in notebook["crumbs"] and "Matematik" in notebook["crumbs"] and "Türev" in notebook["crumbs"]
    assert notebook["itemsQuery"]["subjectId"] == "matematik"
    assert notebook["itemsQuery"]["sectionId"] == "SEC1"
    assert "unsectioned" not in notebook["itemsQuery"]
    assert len(notebook["tiles"]) == 2
    assert "Çözüldü" in notebook["tiles"][0] and "2 fotoğraf" in notebook["tiles"][0]
    assert "Zincir kuralı" in notebook["tiles"][0]
    assert notebook["images"] >= 1
    assert notebook["solvedQuery"]["solved"] is True
    assert notebook["unsectionedQuery"]["unsectioned"] is True and "sectionId" not in notebook["unsectionedQuery"]


def test_soru_defteri_buyuk_goruntu(notebook: dict[str, Any]) -> None:
    assert notebook["itemFetched"] is True
    assert "Zincir kuralı — tekrar çöz." in notebook["drawer"]
    assert "Çözüldü" in notebook["drawer"] and "Türev" in notebook["drawer"]
    assert "salt okunur" in notebook["drawer"]
    assert "Sonraki soru" in notebook["drawer"]


def test_soru_defteri_erisilebilir(notebook: dict[str, Any]) -> None:
    assert notebook["violations1"] == []
    assert notebook["violations2"] == []


def test_bayrak_kapaliysa_sekme_yok() -> None:
    out = _run(FLAG_OFF)
    assert "Soru defteri" not in out["tabs"]
    assert out["tabs"][0] == "Ajanda"


def test_ayarlar_soru_defteri_bayragi() -> None:
    out = _run(SETTINGS)
    rows = out["rows"]
    notebook = next(row for row in rows if row.startswith("Soru defteri"))
    assert "salt okur" in notebook
    keys = [row.split("Açık")[0] for row in rows]
    assert keys.index("Soru defteri") == keys.index("Velinin hata defteri görselleri") + 1
