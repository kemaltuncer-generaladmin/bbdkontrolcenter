"""Hata defteri gelen kutusu (Akademi README §14 madde 104) — KM paneli.

Kullanıcı (16.10.2026): "öğretmen hata defterine çok karışık bi yoldan erişiyor gelen kutusuna fln da düşsün göz önünde
olsun. ayrıca öğretmen çözüm için görsel ses kaydı vs iletebilsin." KM paneli mobil öğretmen ekranıyla AYNI uçları
kullanır (A13):

- `mistake-inbox-logic.js` (saf, Node): süzgeç sorgusu, video bağlantısı denetimi, yükleme alanları, çözüm gövdesi,
  kaynak etiketi, varsayılan yanıtlayan, son çözüm özeti;
- `mistake-inbox.js` (DOM taklidiyle): liste → ayrıntı çekmecesi → öğretmen adına çözüm (metin + video bağlantısı;
  varsayılan yanıtlayan kaydın sorumlusu), "Yanıt gerekmiyor";
- vekil: altı işlem izin listesinde, çok parçalı yükleme işaretli, KM izni `learning`, gezinmede yeni ekran.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import LEARNING, km_permissions
from modules.bbd_akademi.tests.test_akademi_hoca_cozum import PANEL, PRELUDE, node_run

LOGIC = r"""
import * as m from './mistake-inbox-logic.mjs';
const out = {
  filters: [m.filterQuery('waiting'), m.filterQuery('new', { staffId: 's1', subjectId: 'fizik' }), m.filterQuery('yok')],
  video: [m.validVideoUrl('https://www.youtube.com/watch?v=x'), m.validVideoUrl('javascript:alert(1)'),
    m.validVideoUrl('youtube.com'), m.validVideoUrl('https://a b')],
  fields: [m.uploadFields('voice', { durationSeconds: 37.4, staffId: 's1' }), m.uploadFields('image'),
    m.uploadFields('voice', { durationSeconds: 500 })],
  bodies: [m.answerBody({ clientUuid: 'k1', text: '  çözüm  ', fileIds: ['f1'], staffId: 's1' }),
    m.answerBody({ clientUuid: 'k2', text: '   ', fileIds: [] }),
    m.answerBody({ clientUuid: 'k3', videoUrl: 'https://v.test/1' }),
    m.answerBody({ clientUuid: 'k4', videoUrl: 'kötü bağlantı' })],
  source: [m.sourceLabel({ kind: 'resource', resourceTitle: 'Bilfen', page: 48, questionNo: '7' }),
    m.sourceLabel({ kind: 'exam', examName: 'TYT 3', examQuestionNo: 12 }), m.sourceLabel(null)],
  label: m.mistakeLabel({ subject: { name: 'Fizik' }, topic: { name: 'Newton' }, source: { kind: 'other', description: 'Defter' } }),
  responder: [m.defaultResponder({ assignedTo: { id: 's1' } }), m.defaultResponder(null)],
  summary: m.answerSummary({ mistake: { teacherAnswers: [{ author: { displayName: 'Ali Koç' }, text: 'Kısa',
    images: [{}], voice: [{}], files: [{}], videoUrl: 'https://v' }] } }),
};
process.stdout.write(JSON.stringify(out));
"""


def test_saf_mantik(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (tmp_path / "mistake-inbox-logic.mjs").write_bytes((PANEL / "mistake-inbox-logic.js").read_bytes())
    (tmp_path / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert out["filters"] == [{"status": "waiting"}, {"status": "new", "staffId": "s1", "subjectId": "fizik"},
                              {"status": "waiting"}]
    assert out["video"] == [True, False, False, False]
    assert out["fields"] == [{"durationSeconds": "37", "staffId": "s1"}, {}, {"durationSeconds": "120"}]
    assert out["bodies"][0] == {"clientUuid": "k1", "attachmentFileIds": ["f1"], "text": "çözüm", "staffId": "s1"}
    assert out["bodies"][1] is None
    assert out["bodies"][2] == {"clientUuid": "k3", "attachmentFileIds": [], "videoUrl": "https://v.test/1"}
    assert out["bodies"][3] is None
    assert out["source"] == ["Bilfen · s. 48 · 7. soru", "TYT 3 · 12. soru", ""]
    assert out["label"] == "Fizik · Newton · Defter"
    assert out["responder"] == ["s1", ""]
    assert out["summary"] == "Ali Koç: Kısa · 1 görsel · sesli anlatım · 1 PDF · video"


def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    for op_id in ("admin_listMistakeInbox", "admin_getMistakeInboxItem", "admin_markMistakeInboxSeen",
                  "admin_closeMistakeInboxItem", "admin_answerMistake", "admin_uploadMistakeAnswerFile"):
        assert op_id in ops, op_id
        assert ops[op_id].feature_flag == "coach_questions"
        assert km_permissions(ops[op_id]) == (LEARNING,)
    assert ops["admin_uploadMistakeAnswerFile"].multipart is True
    assert ops["admin_answerMistake"].multipart is False
    assert ops["admin_answerMistake"].path == "/v1/admin/mistake-inbox/{mistakeId}/answers"
    sections = (PANEL / "sections.js").read_text(encoding="utf-8")
    assert "key: 'mistakeInbox'" in sections and "./mistake-inbox.js" in sections


UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const item = (extra = {}, answers = []) => ({
    mistake: { id: 'm1', clientUuid: null, subject: { id: 'fizik', name: 'Fizik' },
      topic: { id: 'tp', name: 'Newton', subjectId: 'fizik' },
      source: { kind: 'resource', resourceTitle: 'Bilfen', page: 48, questionNo: '7' },
      correctAnswer: 'C', studentAnswer: 'A', solutionNote: 'Kuvveti ters yazdım', tags: [], reason: 'careless',
      images: [], imagesHidden: false, origin: 'manual', status: 'active',
      review: { stage: 1, intervalDays: 1, dueDate: null, consecutiveSolved: 0, checkup: false, reviewCount: 0,
        lastReviewedAt: null, lastResult: null },
      sharedWithGuardian: false, learnedAt: null, createdAt: '2026-10-16T08:00:00Z', updatedAt: null,
      teacherAnswers: answers },
    student: { id: 'stu-1', displayName: 'Elif Kaya', classGroup: { id: 'c', name: '11-SAY' } },
    assignedTo: { id: 's-ali', displayName: 'Ali Koç' }, route: 'pool', status: 'new',
    receivedAt: '2026-10-16T08:00:00Z', seenAt: null, answeredAt: null, closedAt: null, questionId: null, ...extra });
  routes['GET /v1/admin/mistake-inbox'] = { items: [item()], nextCursor: null };
  routes['GET /v1/admin/mistake-inbox/m1'] = item();
  routes['POST /v1/admin/mistake-inbox/m1/answers'] = (body) => item({ status: 'answered', answeredAt: '2026-10-16T09:00:00Z' },
    [{ id: 'a1', author: { id: 's-ali', displayName: 'Ali Koç' }, role: 'teacher', text: body.text, images: [], voice: [],
      files: [], videoUrl: body.videoUrl || null, createdAt: '2026-10-16T09:00:00Z', attachmentsExpired: false }]);

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const toast = toaster(root);
  const teachers = [{ value: 's-ali', label: 'Ali Koç' }, { value: 's-merve', label: 'Merve Ak' }];
  const app = { root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { teachers: async () => [], staff: async () => [], async options() { return teachers; } } };
  const { section } = await load('./panels/bbd_akademi/mistake-inbox.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(60);
  const out = {};
  out.title = root.querySelector('.ak-title')?.textContent;
  out.rows = root.querySelectorAll('button.kit-row').length;
  out.rowText = root.querySelector('button.kit-row')?.textContent || '';
  out.listQuery = sent.find((c) => c.path === '/v1/admin/mistake-inbox')?.query;
  out.a11yList = audit(view.node);
  click(root.querySelector('button.kit-row'));
  await sleep(60);
  const drawer = root.querySelector('.kit-drawer');
  out.drawerTitle = drawer?.querySelector('.kit-drawer-title')?.textContent;
  out.drawerText = drawer?.textContent.includes('Öğrencinin notu: Kuvveti ters yazdım');
  out.closeButton = Boolean(byText(drawer, 'button', 'Yanıt gerekmiyor'));
  out.a11yDrawer = audit(drawer);
  const textarea = drawer.querySelector('textarea');
  textarea.value = 'Önce serbest cisim diyagramı çiz.';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  const video = drawer.querySelector('input[type="url"]');
  video.value = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
  video.dispatchEvent(new Event('input', { bubbles: true }));
  click(byText(drawer, 'button', 'Çözümü gönder'));
  await sleep(80);
  out.answers = sent.filter((c) => c.path === '/v1/admin/mistake-inbox/m1/answers').map((c) => c.body);
  out.afterAnswer = root.querySelector('.kit-drawer')?.textContent.includes('Video bağlantısı');
  out.rowAfter = root.querySelector('button.kit-row')?.textContent || '';
  out.toasts = toasts.map(([text, tone]) => [text, tone]);
  return out;
}
"""


def test_liste_ve_ogretmen_adina_cozum() -> None:
    out = node_run(UI)
    assert out["title"] == "Hata defteri gelen kutusu"
    assert out["rows"] == 1
    assert "Elif Kaya" in out["rowText"] and "Yeni" in out["rowText"] and "Fizik · Newton" in out["rowText"]
    assert out["listQuery"]["status"] == "waiting"
    assert out["a11yList"] == [] and out["a11yDrawer"] == []
    assert out["drawerTitle"] == "Elif Kaya" and out["drawerText"] and out["closeButton"]
    (body,) = out["answers"]
    assert body["staffId"] == "s-ali"                # varsayılan: kaydın sorumlusu
    assert body["text"] == "Önce serbest cisim diyagramı çiz."
    assert body["videoUrl"].startswith("https://www.youtube.com/")
    assert out["afterAnswer"]
    assert "Yanıtlandı" in out["rowAfter"]
    assert ["Çözüm gönderildi; öğrenciye bildirim gitti.", "good"] in out["toasts"]
