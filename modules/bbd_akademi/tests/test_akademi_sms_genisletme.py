"""Kurum SMS'i genişletmesi (bbd-akademi README §14 madde 88; ikinci uçtan uca denetim V8, İ2, İ6 — Q5).

Node'da küçük DOM'la (`tools/a11y/harness.py`):

* Duyurular: teslim özeti (gönderilen SMS parçası), bekleme metni (neden + an), teslim raporu CSV'si (Excel uyumlu,
  maskeli telefon, Türkçe durum / neden).
* Ayarlar › SMS: imza alanının boş / "yok" çevirisi, bütçe kartı satırları (KM bütçesi + amaç başına 24 saat; eski
  KM'de bütçe yok uyarısı), sekme çizimi: bütçe, kurum SMS ayarları (ödeme hatırlatması, deneme sonucu), kurum SMS
  kuyruğu listesi — istekler doğru uçlara gider.
`node` yoksa atlanır.
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
const qs = (query) => {
  const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?${parts.join('&')}` : '';
};
function makeApp(root, api) {
  const get = (p, q) => api(`${p}${qs(q)}`);
  const client = {
    can: () => true, get, getCached: get, invalidate: () => {},
    pages(path, query = {}, { limit = 50 } = {}) {
      let done = false;
      return { get done() { return done; }, async next() { done = true; return (await get(path, { ...query, limit }))?.items || []; } };
    },
    async all(path, query = {}, { limit = 100 } = {}) { return (await get(path, { ...query, limit }))?.items || []; },
  };
  return { root, api: client, can: () => true, has: () => true, enabled: () => true, go: () => {}, toast: () => {},
    lookups: {} };
}
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ');

export default async function ({ document, sleep, api, load, calls }) {
  const out = {};
  const ann = await load('./panels/bbd_akademi/comms/announcements.js');
  const base = { queued: 3, sent: 10, failed: 1, skipped: 0, recipients: 14, sentSegments: 20 };
  out.delivery = ann.smsDeliveryText({ ...base, deferred: 3, deferredReason: 'sms_budget_exceeded',
    deferredUntil: '2026-10-15T11:00:00Z' });
  out.deferred = ann.smsDeferredText({ ...base, deferred: 3, deferredReason: 'sms_budget_exceeded',
    deferredUntil: '2026-10-15T11:00:00Z' });
  out.noDeferred = ann.smsDeferredText({ ...base, deferred: 0, deferredReason: null, deferredUntil: null });
  out.csv = ann.smsRecipientsCsv([
    { guardianName: 'Ayşe Kaya', studentNames: ['Elif Kaya', 'Can Kaya'], phoneMasked: '+90 532 *** ** 01',
      status: 'failed', reasonLabel: 'Velinin telefonu kayıtlı değil', attempts: 1, segments: 1, sentAt: null,
      nextAttemptAt: null },
    { guardianName: '=Mehmet', studentNames: [], phoneMasked: null, status: 'sent', reasonLabel: null,
      attempts: 1, segments: 2, sentAt: '2026-10-15T08:00:00Z', nextAttemptAt: null },
  ]);
  const sms = await load('./panels/bbd_akademi/admin/settings-sms.js');
  out.sig = [sms.signatureFrom(null), sms.signatureFrom(''), sms.signatureFrom('BBD'),
    sms.signatureTo(''), sms.signatureTo(' YOK '), sms.signatureTo(' BBD ')];
  out.rows = sms.budgetRows({
    textBudget: { enabled: true, dailyBudget: 2000, used: 1950, remaining: 50, perPhonePerDay: 5 },
    usage: [{ purpose: 'announcement', sent: 40, segments: 80, failed: 2, waiting: 0 },
            { purpose: 'payment_reminder', sent: 5, segments: 5, failed: 0, waiting: 3 }],
  }).kpis.map((k) => [k.label, k.value, k.tone]);
  out.oldKm = sms.budgetRows({ textBudget: null, usage: [] });

  const root = document.createElement('div');
  document.body.append(root);
  const tab = sms.smsTab(makeApp(root, api));
  root.append(tab.node);
  tab.reload();
  await sleep(50);
  out.page = text(tab.node);
  out.calls = calls.map((c) => c.path);
  out.checkboxes = tab.node.querySelectorAll('input').filter((i) => i.type === 'checkbox').length;
  return out;
}
"""

HEALTH = {
    "windowSeconds": 3600, "sent": 0, "delivered": 0, "failed": 0, "pending": 0, "late": 0, "untracked": 0,
    "otpChannel": 0, "deliveryRate": None, "avgDeliverySeconds": None, "maxDeliverySeconds": None,
    "smsRemaining": 6000, "smsLow": False, "providerOk": True,
    "textBudget": {"enabled": True, "dailyBudget": 2000, "used": 2000, "remaining": 0, "perPhonePerDay": 5},
    "usage": [{"purpose": "announcement", "sent": 1990, "segments": 2000, "failed": 0, "waiting": 12},
              {"purpose": "payment_reminder", "sent": 0, "segments": 0, "failed": 0, "waiting": 0},
              {"purpose": "exam_result", "sent": 0, "segments": 0, "failed": 0, "waiting": 0}],
}
SETTINGS = {
    "name": "Benim Başarı Dünyam", "shortName": "BBD", "timezone": "Europe/Istanbul",
    "sms": {"signature": None,
            "paymentReminders": {"enabled": True, "audience": "all", "dueSoon": True, "overdue": False},
            "examResults": {"enabled": False, "audience": "non_app"}},
}
OUTBOX = {"items": [{
    "id": "o1", "purpose": "payment_reminder", "createdAt": "2026-10-15T06:00:00Z", "guardianName": "Ayşe Kaya",
    "studentName": "Can Kaya", "phoneMasked": "+90 532 *** ** 01", "status": "queued",
    "reason": "sms_budget_exceeded", "reasonLabel": "Günlük SMS bütçesi doldu", "segments": 1, "sentAt": None,
    "nextAttemptAt": "2026-10-15T08:00:00Z"}], "nextCursor": None}


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {
        "GET /v1/admin/sms/health": HEALTH,
        "GET /v1/admin/settings": SETTINGS,
        "GET /v1/admin/sms/outbox*": OUTBOX,
        "GET /v1/admin/sms/otp-log*": {"items": [], "nextCursor": None},
    }
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_duyuru_teslim_ozeti_ve_bekleme(result: dict[str, Any]) -> None:
    assert result["delivery"] == "10 gönderildi · 3 bekliyor · 1 başarısız (14 veli, 20 SMS)"
    assert result["deferred"].startswith("3 veli bekliyor: günlük SMS bütçesi doldu · ") and "2026" in result["deferred"]
    assert result["deferred"].endswith("sonrası gönderilir") and result["noDeferred"] == ""


def test_teslim_raporu_csv(result: dict[str, Any]) -> None:
    lines = result["csv"].split("\r\n")
    assert lines[0] == "﻿Veli;Öğrenci;Telefon;Durum;Neden;Deneme;SMS;Gönderim;Yeniden deneme"
    assert lines[1] == "Ayşe Kaya;Elif Kaya, Can Kaya;'+90 532 *** ** 01;Başarısız;Velinin telefonu kayıtlı değil;1;1;;"
    assert lines[2].startswith("'=Mehmet;;;Gönderildi;;1;2;") and "2026" in lines[2]  # formül etkisiz


def test_imza_alani(result: dict[str, Any]) -> None:
    assert result["sig"] == ["", "yok", "BBD", None, "", "BBD"]


def test_butce_satirlari(result: dict[str, Any]) -> None:
    assert result["rows"][:2] == [["Günlük SMS bütçesi (24 sa)", "1.950 / 2.000", "warn"], ["Kalan", "50", ""]]
    assert result["rows"][2][:2] == ["Numara başına günde", "5"]
    assert result["rows"][3] == ["Duyuru (24 sa)", "40 veli · 80 SMS · 2 başarısız", "warn"]
    assert result["rows"][4] == ["Ödeme hatırlatması (24 sa)", "5 veli · 5 SMS · 3 bekliyor", ""]
    assert result["oldKm"] == {"kpis": [], "enabled": None, "known": False}


def test_sms_sekmesi(result: dict[str, Any]) -> None:
    page = result["page"]
    assert "Günlük SMS bütçesi doldu" in page and "Kurum SMS'i: bütçe ve son 24 saat" in page
    assert "Ödeme hatırlatmasını SMS ile de gönder" in page and "Deneme sonucunu veliye SMS ile de gönder" in page
    assert "Ayşe Kaya" in page and "Can Kaya" in page and "Kurum SMS kuyruğu" in page
    assert {"/v1/admin/sms/health", "/v1/admin/settings"} <= set(result["calls"])
    assert any(c.startswith("/v1/admin/sms/outbox") for c in result["calls"])
    assert result["checkboxes"] >= 4
