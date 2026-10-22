"""BBD Akademi 74 — öğrenciye en çok 3 koç (Akademi README §14 madde 134) KM ekranları (Node'da davranış).

Kullanıcı (22.10.2026): "1 öğrenciye 3 adet koç atanabilsin".

- Öğrenci kartı → Koçluk → Koç: öğrencinin koçları (birincil rozetli, "2/3 koç"), her koçta "Koçu değiştir" /
  "Eşlemeyi bitir" yalnız o eşlemeye (`assignmentId`, `/coach-assignments/{id}/end`), "Koç ekle" 3 koçta kapanır
  ("En çok 3 koç atanabilir"); seçicide öğrencinin mevcut koçları yok.
- Koçluk → Koçlar → Eşleme tablosu: satırda 0–3 koç çipi (çip menüsü değiştir / bitir), "+ Koç ekle" / "+ Koç ata",
  3 koçta "3/3".
- Toplu atama "Ek koç olarak ekle" (`onExisting: add`): gerekçe istemez; atlama nedenleri web ile aynı metin.
"""

from __future__ import annotations

from typing import Any

from modules.bbd_akademi.tests.test_akademi_ogretmen_koc import PRELUDE, node_run

COMMON = r"""
  const ref = (id, displayName) => ({ id, displayName });
  const coachRow = (id, name, count, max = null) => ({ coach: { id, displayName: name, photo: null, subjects: [] },
    studentCount: count, maxStudents: max, pendingReviews: 0, openQuestions: 0, medianFirstResponseHours: null,
    weeksPublishedPercent: null, studentAdherencePercent: null, meetings30d: 0 });
  routes['GET /v1/admin/coaching/coaches'] = { items: [coachRow('s-huseyin', 'Hüseyin Hoca', 20, 20),
    coachRow('s-merve', 'Merve Ak', 2), coachRow('s-ayse', 'Ayşe Er', 5), coachRow('s-can', 'Can Uz', 1)] };
  const assignment = (id, coach, extra = {}) => ({ id, student: { id: 'stu-1', displayName: 'Elif Kaya' }, coach,
    status: 'active', startsOn: '2026-10-14', endsOn: null, endReason: null, handoverNote: null, primary: false,
    assignedBy: 'km:user:7:Test%20Y%C3%B6netici', createdAt: '2026-10-14T08:00:00Z', ...extra });
"""

STUDENT = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
""" + COMMON + r"""
  let history = [assignment('a-1', ref('s-huseyin', 'Hüseyin Hoca'), { primary: true }),
    assignment('a-2', ref('s-merve', 'Merve Ak'), { startsOn: '2026-10-20', createdAt: '2026-10-20T08:00:00Z' })];
  routes['GET /v1/admin/students/stu-1/coach-assignments'] = () => ({ items: history });
  routes['POST /v1/admin/coach-assignments'] = (body) => {
    const created = assignment('a-3', ref(body.coachId, 'Ayşe Er'), { startsOn: '2026-10-22',
      createdAt: '2026-10-22T08:00:00Z' });
    history = [...history, created];
    return { assignment: created, capacity: { coach: created.coach, studentCount: 6, maxStudents: null, exceeded: false } };
  };
  routes['POST /v1/admin/students/stu-1/coach-change'] = (body) => {
    const old = history.find((row) => row.id === body.assignmentId);
    const ended = { ...old, status: 'ended', endsOn: '2026-10-22', endReason: body.reason };
    const created = assignment('a-4', ref(body.newCoachId, 'Can Uz'));
    history = [...history.filter((row) => row.id !== old.id), created, ended];
    return { ended, created, transferredTaskCount: 1, archivedThreadCount: 1,
      capacity: { coach: created.coach, studentCount: 2, maxStudents: null, exceeded: false } };
  };
  routes['POST /v1/admin/coach-assignments/a-3/end'] = (body) => {
    history = history.map((row) => (row.id === 'a-3' ? { ...row, status: 'ended', endsOn: body.endsOn,
      endReason: body.reason } : row));
    return history.find((row) => row.id === 'a-3');
  };
  const { app, root, toasts } = await makeApp(load, document);
  const { coachingPanel } = await load('./panels/bbd_akademi/coaching/students-coaching.js');
  const panel = coachingPanel(app, 'stu-1');
  root.append(panel.node);
  panel.shown();
  await sleep(40);
  const out = {};
  const dialog = () => root.querySelector('.kit-dialog');
  const field = (label, selector = 'input, textarea, select') => dialog().querySelectorAll('.kit-field')
    .find((wrap) => wrap.querySelector('.kit-field-label')?.textContent.replace('*', '').trim() === label)
    ?.querySelector(selector);
  const setValue = (node, value, event = 'input') => { node.value = value; node.dispatchEvent(new Event(event, { bubbles: true })); };
  const cardText = () => panel.node.querySelector('.kit-card').textContent;
  out.card1 = cardText();
  out.changeButtons = panel.node.querySelectorAll('button').filter((b) => b.textContent === 'Koçu değiştir').length;
  out.a11y = audit(panel.node);
  // Koç ekle: seçicide mevcut koçlar yok
  click(byText(root, 'button', 'Koç ekle'));
  await sleep(20);
  out.addTitle = dialog().querySelector('.kit-dialog-title').textContent;
  out.addDesc = dialog().textContent;
  out.addOptions = dialog().querySelector('select').querySelectorAll('option').map((o) => o.textContent);
  setValue(dialog().querySelector('select'), 's-ayse', 'change');
  click(byText(dialog(), 'button', 'Ekle'));
  await sleep(60);
  out.addBody = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/coach-assignments').map((c) => c.body);
  out.card3 = cardText();
  out.addAfter = Boolean(byText(root, 'button', 'Koç ekle'));
  // ikinci koçu (Merve, a-2) değiştir
  const changes = panel.node.querySelectorAll('button').filter((b) => b.textContent === 'Koçu değiştir');
  click(changes[1]);
  await sleep(20);
  out.changeDesc = dialog().textContent;
  out.changeOptions = dialog().querySelector('select').querySelectorAll('option').map((o) => o.textContent);
  setValue(dialog().querySelector('select'), 's-can', 'change');
  setValue(field('Gerekçe'), 'Koç izinli');
  setValue(field('Devir notu'), 'Paragraf çalışması sürüyor');
  click(byText(dialog(), 'button', 'Değiştir'));
  await sleep(60);
  out.changeBody = sent.filter((c) => c.path === '/v1/admin/students/stu-1/coach-change').map((c) => c.body);
  // Ayşe'nin (a-3) eşlemesini bitir
  const rows = panel.node.querySelectorAll('.ak-sco-coach');
  const ayse = rows.find((row) => row.textContent.includes('Ayşe Er'));
  click(ayse.querySelectorAll('button').find((b) => b.textContent === 'Eşlemeyi bitir'));
  await sleep(20);
  out.endDesc = dialog().textContent;
  setValue(field('Gerekçe'), 'Dönem sonu');
  click(byText(dialog(), 'button', 'Bitir'));
  await sleep(60);
  out.endPaths = sent.filter((c) => c.path.endsWith('/end')).map((c) => c.path);
  out.cardEnd = cardText();
  out.toasts = toasts.map(([text, tone]) => [text, tone]);
  return out;
}
"""

ROSTER = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
""" + COMMON + r"""
  const student = (id, name) => ({ id, displayName: name, classGroup: { id: 'c-9a', name: '9-A' } });
  const three = [assignment('r-1', ref('s-huseyin', 'Hüseyin Hoca'), { primary: true }),
    assignment('r-2', ref('s-merve', 'Merve Ak')), assignment('r-3', ref('s-ayse', 'Ayşe Er'), { status: 'scheduled',
      startsOn: '2026-11-01' })].map((row) => ({ ...row, student: student('stu-1', 'Elif Kaya') }));
  const one = { ...assignment('r-4', ref('s-merve', 'Merve Ak'), { primary: true }), student: student('stu-2', 'Ali Can') };
  routes['GET /v1/admin/coach-assignments/roster'] = { items: [
    { student: student('stu-1', 'Elif Kaya'), assignment: three[0], assignments: three },
    { student: student('stu-2', 'Ali Can'), assignment: one, assignments: [one] },
    // eski yanıt biçimi: yalnız tekil `assignment`
    { student: student('stu-3', 'Zeynep Ay'), assignment: null },
  ], nextCursor: null };
  routes['POST /v1/admin/coach-assignments'] = (body) => ({ assignment: assignment('r-9', ref(body.coachId, 'Can Uz')),
    capacity: null });
  routes['POST /v1/admin/coach-assignments/r-2/end'] = (body) => ({ ...three[1], status: 'ended', endsOn: body.endsOn });
  const { app, root } = await makeApp(load, document);
  const { tab } = await load('./panels/bbd_akademi/coaching/coaching-coaches.js');
  const view = tab(app, { openStudent: (s) => { app.opened = s?.id; } });
  root.append(view.node);
  view.shown();
  await sleep(30);
  click(root.querySelectorAll('.kit-chip').find((chip) => chip.textContent.startsWith('Eşleme tablosu')));
  await sleep(60);
  const out = {};
  const dialog = () => root.querySelector('.kit-dialog');
  const field = (label, selector = 'input, textarea, select') => dialog().querySelectorAll('.kit-field')
    .find((wrap) => wrap.querySelector('.kit-field-label')?.textContent.replace('*', '').trim() === label)
    ?.querySelector(selector);
  const setValue = (node, value, event = 'input') => { node.value = value; node.dispatchEvent(new Event(event, { bubbles: true })); };
  const cells = root.querySelectorAll('.ak-coach-chips');
  out.rows = cells.map((cell) => ({
    chips: cell.querySelectorAll('.ak-coach-chip').map((chip) => chip.querySelector('button').textContent),
    add: cell.querySelectorAll('.ak-coach-add').map((b) => b.textContent),
    text: cell.textContent,
  }));
  out.a11y = audit(root);
  // Elif'in 2. koçu (Merve, r-2) → menü → Eşlemeyi bitir
  const merve = cells[0].querySelectorAll('.ak-coach-chip')[1];
  click(merve.querySelector('button'));
  await sleep(5);
  out.menu = merve.querySelectorAll('.kit-menu-item').map((item) => item.textContent);
  click(merve.querySelectorAll('.kit-menu-item').find((item) => item.textContent === 'Eşlemeyi bitir'));
  await sleep(30);
  out.endDesc = dialog().textContent;
  setValue(field('Gerekçe'), 'Dönem sonu');
  click(byText(dialog(), 'button', 'Bitir'));
  await sleep(60);
  out.endPath = sent.filter((c) => c.path.endsWith('/end')).map((c) => c.path);
  out.opened = app.opened || null;   // çip tıklaması satırı (öğrenci kartı) açmaz
  // Ali'ye koç ekle: Merve seçicide yok
  click(root.querySelectorAll('.ak-coach-chips')[1].querySelector('.ak-coach-add'));
  await sleep(40);
  out.addTitle = dialog().querySelector('.kit-dialog-title').textContent;
  out.addOptions = dialog().querySelector('select').querySelectorAll('option').map((o) => o.textContent);
  setValue(dialog().querySelector('select'), 's-can', 'change');
  click(byText(dialog(), 'button', 'Ekle'));
  await sleep(60);
  out.addBody = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/coach-assignments').map((c) => c.body);
  // Toplu atama gövdesi: "Ek koç olarak ekle" gerekçe istemez
  const logic = await load('./panels/bbd_akademi/coaching/coaching-logic.js');
  out.bulkAdd = logic.bulkAssignBody({ studentIds: ['stu-2'], coachId: 's-can', onExisting: 'add' });
  out.bulkChange = logic.bulkAssignBody({ studentIds: ['stu-2'], coachId: 's-can', onExisting: 'change' });
  out.skipReasons = logic.SKIP_REASONS;
  return out;
}
"""


def test_ogrenci_karti_uc_koc() -> None:
    out: dict[str, Any] = node_run(STUDENT)
    assert out["a11y"] == []
    assert "Koçları" in out["card1"] and "2/3 koç" in out["card1"] and "Birincil" in out["card1"]
    assert "Hüseyin Hoca" in out["card1"] and "Merve Ak" in out["card1"]
    assert out["changeButtons"] == 2
    assert out["addTitle"] == "Koç ekle"
    assert "diğer koçları sürer" in out["addDesc"] and "En çok 3 koç atanabilir" in out["addDesc"]
    # mevcut koçlar seçicide yok
    assert out["addOptions"] == ["Öğretmen / Koç seçin", "Ayşe Er · 5 öğrenci", "Can Uz · 1 öğrenci"]
    assert out["addBody"][0]["studentId"] == "stu-1" and out["addBody"][0]["coachId"] == "s-ayse"
    assert "3/3 koç" in out["card3"] and "En çok 3 koç atanabilir" in out["card3"]
    assert out["addAfter"] is False
    assert ["Ayşe Er eklendi.", "good"] in out["toasts"]
    # değiştir yalnız o eşleme
    assert "Değişen koç: Merve Ak" in out["changeDesc"] and "diğer koçları sürer" in out["changeDesc"]
    assert out["changeOptions"] == ["Öğretmen / Koç seçin", "Can Uz · 1 öğrenci"]
    change = out["changeBody"][0]
    assert change["assignmentId"] == "a-2" and change["newCoachId"] == "s-can"
    assert "öğrencinin diğer koçları sürer" in out["endDesc"] and "Yalnız Ayşe Er ile yazışmalar" in out["endDesc"]
    assert out["endPaths"] == ["/v1/admin/coach-assignments/a-3/end"]
    assert "2/3 koç" in out["cardEnd"] and "Ayşe Er" not in out["cardEnd"]


def test_esleme_tablosu_koc_cipleri() -> None:
    out: dict[str, Any] = node_run(ROSTER)
    assert out["a11y"] == []
    elif_, ali, zeynep = out["rows"]
    assert len(elif_["chips"]) == 3 and elif_["chips"][0].startswith("Hüseyin Hoca ★")
    assert "(01.11.2026)" in elif_["chips"][2]          # ileri tarihli eşleme
    assert elif_["add"] == [] and "3/3" in elif_["text"]
    assert ali["chips"] == ["Merve Ak ▾"] and ali["add"] == ["+ Koç ekle"]
    assert zeynep["chips"] == [] and "Koçu yok" in zeynep["text"] and zeynep["add"] == ["+ Koç ata"]
    assert out["menu"] == ["Koçu değiştir", "Eşlemeyi bitir"]
    assert "öğrencinin diğer koçları sürer" in out["endDesc"]
    assert out["endPath"] == ["/v1/admin/coach-assignments/r-2/end"]
    assert out["opened"] is None
    assert out["addTitle"] == "Koç ekle"
    assert "Merve Ak · 2 öğrenci" not in out["addOptions"] and "Can Uz · 1 öğrenci" in out["addOptions"]
    assert out["addBody"][-1] == {"studentId": "stu-2", "coachId": "s-can", "startsOn": out["addBody"][-1]["startsOn"]}
    assert out["bulkAdd"]["body"]["onExisting"] == "add" and "changeReason" not in out["bulkAdd"]["body"]
    assert "error" in out["bulkChange"]
    assert out["skipReasons"]["limit_reached"] == "En çok 3 koç atanabilir"
