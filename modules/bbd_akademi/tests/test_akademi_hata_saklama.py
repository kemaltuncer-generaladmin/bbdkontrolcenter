"""BBD Akademi hata defteri saklama — KM ayar ekranı ve öğrenci hata çekmecesi (Node'da davranış).

Kullanıcı (14.10.2026): "Hata defterinde çözüldükçe, bir şekilde eski soruların silinmesiyle falan eskileri
silsin." Akademi README §14 madde 64: kurum ayarı `TenantSettings.mistakes` (aç/kapa, "Öğrenildi" fotoğrafı /
kaydı, dokunulmayan eski hata, uyarı süresi) KM → Ayarlar → Kurum'dan yönetilir (`admin_patchSettings`). Boş süre
= o kural kapalı (`null`). Hata çekmecesi "Fotoğraf saklama süresi doldu" notunu ve "Sakla" / silinme gününü
gösterir; özet satırı temizlenen ve yakında silinecek sayıları söyler.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

SCENARIO = r"""
export default async function ({ document, audit, sleep, load }) {
  const SETTINGS = {
    name: 'BBD', shortName: null, timezone: 'Europe/Istanbul', address: null,
    support: {}, quietHoursDefault: { start: '22:00', end: '07:30' },
    finance: { receiptPrefix: 'AK', bookChargeDueDays: 30, reminderDaysBefore: [3, 1], overdueReminderEveryDays: 7 },
    books: { requestExpiryDays: 7, maxQuantity: 5 },
    attendance: { lateAfterMinutes: 10, notifyGuardiansOnAbsence: true },
    reports: { weeklyReportEnabled: true, weeklyReportDay: 'sun', weeklyReportTime: '19:00' },
    assignments: { autoApplyBundlesToNewStudents: false },
    mistakes: { retentionEnabled: true, learnedImageDays: 30, learnedRecordDays: 180, staleRecordDays: 365,
                staleWarningDays: 7 },
  };
  const patches = [];
  const app = {
    can: () => true,
    status: {},
    toast: () => {},
    api: {
      can: () => true,
      featureOff: () => false,
      invalidate: () => {},
      getCached: async () => JSON.parse(JSON.stringify(SETTINGS)),
      get: async () => JSON.parse(JSON.stringify(SETTINGS)),
      patch: async (path, body) => {
        patches.push({ path, body: JSON.parse(JSON.stringify(body)) });
        return { ...SETTINGS, mistakes: { ...SETTINGS.mistakes, ...(body.mistakes || {}) } };
      },
    },
  };
  const { section } = await load('./panels/bbd_akademi/settings.js');
  const root = document.createElement('div');
  document.body.append(root);
  const view = section(app);
  root.append(view.node);
  view.shown({ tab: 'institution' });
  await sleep(30);
  const out = {};
  const cardOf = (title) => root.querySelectorAll('.kit-card, section, .card').find(
    (c) => (c.querySelector('h2, h3, .kit-card-title, header')?.textContent || '').includes(title));
  const box = cardOf('Hata defteri saklama');
  out.found = Boolean(box);
  out.text = box ? box.textContent : '';
  const inputs = box.querySelectorAll('input');
  out.inputs = inputs.map((i) => ({ type: i.type, value: i.type === 'checkbox' ? i.checked : i.value }));
  // fotoğraf süresini 14 yap, eski kayıt kuralını kapat (boş)
  const numbers = inputs.filter((i) => i.type !== 'checkbox');
  numbers[0].value = '14';
  numbers[0].dispatchEvent(new Event('input', { bubbles: true }));
  numbers[2].value = '';
  numbers[2].dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(5);
  const save = root.querySelectorAll('button').find((b) => b.textContent === 'Kaydet');
  save.click();
  await sleep(30);
  out.patches = patches;
  // geçersiz: arşiv kaydı 30 gün (en az 60) → kaydetmez
  const again = cardOf('Hata defteri saklama').querySelectorAll('input').filter((i) => i.type !== 'checkbox');
  again[1].value = '30';
  again[1].dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(5);
  root.querySelectorAll('button').find((b) => b.textContent === 'Kaydet').click();
  await sleep(30);
  out.patchesAfterInvalid = patches.length;
  out.error = cardOf('Hata defteri saklama').textContent.includes('En az 60 olmalı.');
  out.violations = audit(root);
  return out;
}
"""


DRAWER = r"""
export default async function ({ document, audit, sleep, load }) {
  const SID = 'S1';
  const M = {
    id: 'M1', clientUuid: null, subject: { id: 'fizik', name: 'Fizik', shortName: 'FİZ' },
    topic: { id: 't', name: 'Newton', subjectId: 'fizik' }, source: null, correctAnswer: null, studentAnswer: null,
    solutionNote: null, tags: [], reason: null, images: [], imagesHidden: false, origin: 'manual', status: 'learned',
    review: { stage: 4, intervalDays: 14, dueDate: null, consecutiveSolved: 3, checkup: false, reviewCount: 3,
              lastReviewedAt: null, lastResult: null },
    sharedWithGuardian: false, learnedAt: '2026-09-01T10:00:00+03:00', createdAt: '2026-08-20T10:00:00+03:00',
    updatedAt: null, keep: false, imagesExpiredAt: '2026-10-02T03:50:00+03:00', scheduledDeletionOn: '2027-03-01',
  };
  const SUMMARY = { computedAt: '2026-10-14T03:50:00+03:00', from: '2026-09-14', to: '2026-10-14', totalCount: 9,
    activeCount: 5, learnedCount: 2, addedCount: 1, newlyLearnedCount: 0, dueCount: 0, expiredCount: 2,
    expiringCount: 3, byReason: [], dominantReason: null, topTopics: [], bySubject: [] };
  const gets = [];
  const app = {
    root: document.body,
    can: () => true,
    toast: () => {},
    api: {
      can: () => true,
      featureOff: () => false,
      getCached: async (path) => app.api.get(path),
      get: async (path, query = {}) => {
        gets.push({ path, query: JSON.parse(JSON.stringify(query)) });
        if (path.endsWith('/mistakes/summary')) return SUMMARY;
        if (path.endsWith('/mistakes/M1')) return M;
        if (path.endsWith('/mistakes') || path.endsWith('/review-queue')) return { items: [M], nextCursor: null };
        return { items: [], nextCursor: null };
      },
      pages: (path, query = {}, { limit = 50 } = {}) => {
        let done = false;
        return {
          get done() { return done; },
          async next() { done = true; return (await app.api.get(path, { ...query, limit })).items; },
        };
      },
    },
  };
  const { learningPanel } = await load('./panels/bbd_akademi/students-learning.js');
  const root = document.createElement('div');
  document.body.append(root);
  const panel = learningPanel(app, SID);
  root.append(panel.node);
  panel.shown({ learningTab: 'mistakes' });
  await sleep(40);
  const out = {};
  out.summaryText = root.textContent.includes('2 eski hata temizlendi') && root.textContent.includes('3 hata yakında silinecek');
  // durum süzgeci: "Yakında silinecekler" → status=all + expiring=true
  const select = root.querySelectorAll('select').find((el) => el.querySelectorAll('option').some((o) => o.value === 'expiring'));
  select.value = 'expiring';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(30);
  out.expiringQuery = gets.filter((g) => g.path === `/v1/students/${SID}/mistakes`).pop().query;
  // satır → çekmece
  const row = root.querySelectorAll('.kit-row, [role="row"], tr').find((r) => r.textContent.includes('Newton'));
  row.click();
  await sleep(30);
  out.drawerText = document.body.textContent;
  return out;
}
"""


@pytest.fixture(scope="module")
def drawer() -> dict[str, Any]:
    try:
        data = run(DRAWER, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    try:
        data = run(SCENARIO, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_kurum_sekmesinde_hata_defteri_saklama_karti(out: dict[str, Any]) -> None:
    assert out["found"]
    assert "Eski hata kayıtlarını kendiliğinden temizle" in out["text"]
    assert "“Öğrenildi” fotoğrafları (gün)" in out["text"] and "Dokunulmayan eski hata (gün)" in out["text"]
    assert out["inputs"] == [
        {"type": "checkbox", "value": True},
        {"type": "text", "value": "30"},
        {"type": "text", "value": "180"},
        {"type": "text", "value": "365"},
        {"type": "text", "value": "7"},
    ]


def test_kaydet_grubu_tam_gonderir_bos_sure_null(out: dict[str, Any]) -> None:
    assert out["patches"][0] == {
        "path": "/v1/admin/settings",
        "body": {"mistakes": {"retentionEnabled": True, "learnedImageDays": 14, "learnedRecordDays": 180,
                              "staleRecordDays": None, "staleWarningDays": 7}},
    }


def test_gecersiz_sure_kaydedilmez(out: dict[str, Any]) -> None:
    assert out["patchesAfterInvalid"] == 1 and out["error"]
    assert out["violations"] == []


def test_ozet_temizlenen_ve_silinecek_sayilari(drawer: dict[str, Any]) -> None:
    assert drawer["summaryText"]


def test_yakinda_silinecekler_suzgeci(drawer: dict[str, Any]) -> None:
    assert drawer["expiringQuery"]["status"] == "all" and drawer["expiringQuery"]["expiring"] is True


def test_cekmece_fotograf_saklama_notu_ve_silinme_gunu(drawer: dict[str, Any]) -> None:
    assert "Fotoğraf saklama süresi doldu (02.10.2026)" in drawer["drawerText"]
    assert "01.03.2027 tarihinde kendiliğinden silinecek" in drawer["drawerText"]
