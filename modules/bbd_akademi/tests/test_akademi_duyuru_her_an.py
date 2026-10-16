"""BBD Akademi duyuru her an şalteri — KM ayar ekranı ve duyuru yayımlama (Node'da davranış).

Kullanıcı (16.10.2026): "sessiz saatlerde duyuru gönderme işini kaldıralım her an gönderilebilsin (ayar şalteri)".
Akademi README §14 madde 121: kurum ayarı `TenantSettings.announcements` (`sendAnytime` varsayılan açık,
`nightStart` / `nightEnd`) KM → Ayarlar → Kurum → "Duyurular" kartından yönetilir (`admin_patchSettings`). Şalter
kapalıyken yayımlama penceresi gece uyarısını (`admin_getAnnouncementDeliveryWindow`) ve "Yine de şimdi gönder"
seçeneğini gösterir (`sendNow`); ertelenmiş duyuruda bildirim / SMS anı (`deliverAt`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

SETTINGS_SCENARIO = r"""
export default async function ({ document, audit, sleep, load }) {
  const BASE = {
    name: 'BBD', shortName: null, timezone: 'Europe/Istanbul', address: null, support: {},
    finance: { receiptPrefix: 'AK', bookChargeDueDays: 30, reminderDaysBefore: [3, 1], overdueReminderEveryDays: 7 },
    books: { requestExpiryDays: 7, maxQuantity: 5 },
    attendance: { lateAfterMinutes: 10, notifyGuardiansOnAbsence: true },
    reports: { weeklyReportEnabled: true, weeklyReportDay: 'sun', weeklyReportTime: '19:00' },
    assignments: { autoApplyBundlesToNewStudents: false },
    mistakes: { retentionEnabled: true, learnedImageDays: 30, learnedRecordDays: 180, staleRecordDays: 365,
                staleWarningDays: 7 },
  };
  async function scene(settings) {
    const patches = [];
    const app = {
      can: () => true, status: {}, toast: () => {},
      api: {
        can: () => true, featureOff: () => false, invalidate: () => {},
        getCached: async () => JSON.parse(JSON.stringify(settings)),
        get: async () => JSON.parse(JSON.stringify(settings)),
        patch: async (path, body) => {
          patches.push({ path, body: JSON.parse(JSON.stringify(body)) });
          return { ...settings, announcements: { ...(settings.announcements || {}), ...(body.announcements || {}) } };
        },
      },
    };
    const { section } = await load('./panels/bbd_akademi/admin/settings.js');
    const root = document.createElement('div');
    document.body.append(root);
    const view = section(app);
    root.append(view.node);
    view.shown({ tab: 'institution' });
    await sleep(30);
    const cardOf = (title) => root.querySelectorAll('.kit-card, section, .card').find(
      (c) => (c.querySelector('h2, h3, .kit-card-title, header')?.textContent || '').includes(title));
    const box = cardOf('Duyurular');
    const out = { found: Boolean(box), text: box ? box.textContent : '' };
    const inputs = box.querySelectorAll('input');
    out.inputs = inputs.map((i) => ({ type: i.type, value: i.type === 'checkbox' ? i.checked : i.value }));
    return { out, root, box, inputs, patches, cardOf };
  }
  const result = {};
  // 1) eski Akademi (announcements yok) → varsayılan "her an" görünür
  const old = await scene(BASE);
  result.old = old.out;
  old.root.remove();
  // 2) şalteri kapat, gece bitişini 08:00 yap → yalnız duyuru grubu birleştirilip gönderilir
  const cur = await scene({ ...BASE, announcements: { sendAnytime: true, nightStart: '22:00', nightEnd: '07:30' } });
  result.current = cur.out;
  const box = cur.inputs.find((i) => i.type === 'checkbox');
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  box.dispatchEvent(new Event('input', { bubbles: true }));
  const texts = cur.inputs.filter((i) => i.type !== 'checkbox');
  texts[1].value = '08:00';
  texts[1].dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(5);
  cur.root.querySelectorAll('button').find((b) => b.textContent === 'Kaydet').click();
  await sleep(30);
  result.patches = cur.patches;
  // 3) bozuk saat kaydedilmez
  const again = cur.cardOf('Duyurular').querySelectorAll('input').filter((i) => i.type !== 'checkbox');
  again[0].value = '25:00';
  again[0].dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(5);
  cur.root.querySelectorAll('button').find((b) => b.textContent === 'Kaydet').click();
  await sleep(30);
  result.patchesAfterInvalid = cur.patches.length;
  result.error = cur.cardOf('Duyurular').textContent.includes('SS:DD biçiminde olmalı');
  result.violations = audit(cur.root);
  return result;
}
"""

PUBLISH_SCENARIO = r"""
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ');
export default async function ({ document, sleep, load }) {
  const ann = await load('./panels/bbd_akademi/comms/announcements.js');
  const result = {
    suffix: ['07:30', '22:00', '06:40', '10:00', '00:00', '08:15', '20:00'].map(ann.clockDe),
    holdNow: ann.NIGHT.holdNow('07:30'),
    label: ann.SMS_DEFERRED.quiet_hours,
  };
  const ITEM = {
    id: 'A1', title: 'Servis saati', bodyMarkdown: 'Yarın servis 08:00.', status: 'draft', pinned: false,
    audience: { recipients: 'guardians', scope: 'tenant', classGroupIds: [], studentGroupIds: [], studentIds: [] },
    scheduledAt: null, publishedAt: null, expiresAt: null, createdAt: '2026-10-16T19:00:00Z', createdBy: 'Hakan',
    readRatePercent: null, sms: { enabled: true, text: 'Servis 08:00', nonAppOnly: false, segmentsPerMessage: 1,
      delivery: null },
  };
  async function scene(window, answer) {
    const posts = [];
    const toasts = [];
    const app = {
      root: document.body, can: () => true, has: () => true, enabled: () => true, go: () => {}, lookups: {},
      toast: (message) => toasts.push(message),
      api: {
        can: () => true, featureOff: () => false, invalidate: () => {},
        get: async (path) => {
          if (path === '/v1/admin/announcements/delivery-window') return JSON.parse(JSON.stringify(window));
          if (path === '/v1/admin/announcements/A1') return JSON.parse(JSON.stringify(ITEM));
          if (path.endsWith('/stats')) throw new Error('yok');
          return { items: [], nextCursor: null };
        },
        getCached: async (path) => app.api.get(path),
        pages: () => ({ done: true, async next() { return []; } }),
        post: async (path, body) => {
          posts.push({ path, body: JSON.parse(JSON.stringify(body)) });
          return { ...ITEM, ...answer };
        },
      },
    };
    const root = document.createElement('div');
    document.body.append(root);
    const view = ann.section(app);
    root.append(view.node);
    view.shown({ announcementId: 'A1' });
    await sleep(40);
    document.body.querySelectorAll('button').find((b) => b.textContent === 'Yayımla').click();
    await sleep(40);
    const dialog = document.body.querySelectorAll('dialog, [role="dialog"], .kit-dialog').pop();
    return { app, root, posts, toasts, dialog, view };
  }
  // şalter kapalı, şu an gece → uyarı + "Yine de şimdi gönder"; işaretleyip yayımla
  const night = { sendAnytime: false, nightStart: '22:00', nightEnd: '07:30', holdUntil: '2026-10-17T04:30:00Z' };
  const a = await scene(night, { status: 'published', publishedAt: '2026-10-16T20:00:00Z', deliverAt: null });
  result.nightDialog = text(a.dialog);
  const urgent = a.dialog.querySelectorAll('label').find((l) => l.textContent.includes('Yine de şimdi gönder'));
  result.hasUrgent = Boolean(urgent);
  const box = urgent?.querySelector('input') || a.dialog.querySelectorAll('input').filter((i) => i.type === 'checkbox').pop();
  box.checked = true;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  box.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(5);
  a.dialog.querySelectorAll('button').filter((b) => b.textContent === 'Yayımla').pop().click();
  await sleep(40);
  result.urgentPosts = a.posts;
  result.urgentToasts = a.toasts;
  a.view.destroy();
  a.root.remove();
  document.body.querySelectorAll('dialog, [role="dialog"], .kit-dialog, .kit-drawer, aside').forEach((n) => n.remove());

  // bekleyerek yayımla → yanıttaki deliverAt ile toast
  const b = await scene(night, { status: 'published', publishedAt: '2026-10-16T20:00:00Z',
    deliverAt: '2026-10-17T04:30:00Z' });
  b.dialog.querySelectorAll('button').filter((x) => x.textContent === 'Yayımla').pop().click();
  await sleep(40);
  result.heldPosts = b.posts;
  result.heldToasts = b.toasts;
  b.view.destroy();
  b.root.remove();
  document.body.querySelectorAll('dialog, [role="dialog"], .kit-dialog, .kit-drawer, aside').forEach((n) => n.remove());

  // varsayılan (her an) → ne uyarı ne seçenek
  const c = await scene({ sendAnytime: true, nightStart: '22:00', nightEnd: '07:30', holdUntil: null },
    { status: 'published', publishedAt: '2026-10-16T20:00:00Z', deliverAt: null });
  result.defaultDialog = text(c.dialog);
  return result;
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
def settings() -> dict[str, Any]:
    return _run(SETTINGS_SCENARIO)


@pytest.fixture(scope="module")
def publish() -> dict[str, Any]:
    return _run(PUBLISH_SCENARIO)


def test_kurum_sekmesinde_duyurular_karti_varsayilan_her_an(settings: dict[str, Any]) -> None:
    for scene in (settings["old"], settings["current"]):
        assert scene["found"], "Duyurular kartı yok"
        assert "Duyurular her an gönderilsin" in scene["text"]
        assert "Yine de şimdi gönder" in scene["text"]
        assert scene["inputs"][0] == {"type": "checkbox", "value": True}
        assert [i["value"] for i in scene["inputs"][1:]] == ["22:00", "07:30"]


def test_salter_kapatilir_grup_birlesik_gider(settings: dict[str, Any]) -> None:
    assert settings["patches"] == [{
        "path": "/v1/admin/settings",
        "body": {"announcements": {"sendAnytime": False, "nightStart": "22:00", "nightEnd": "08:00"}},
    }]


def test_bozuk_saat_kaydedilmez(settings: dict[str, Any]) -> None:
    assert settings["patchesAfterInvalid"] == 1
    assert settings["error"]
    assert settings["violations"] == []


def test_saat_eki_ve_metinler(publish: dict[str, Any]) -> None:
    assert publish["suffix"] == ["07:30'da", "22:00'de", "06:40'ta", "10:00'da", "00:00'da", "08:15'te", "20:00'de"]
    assert publish["holdNow"] == "Şu an gece saati — bildirim ve SMS 07:30'da gidecek."
    assert publish["label"] == "gece aralığı — sabah gidecek (kurum ayarı)"


def test_gece_uyarisi_ve_yine_de_simdi_gonder(publish: dict[str, Any]) -> None:
    assert "Şu an gece saati — bildirim ve SMS 07:30'da gidecek." in publish["nightDialog"]
    assert publish["hasUrgent"]
    assert publish["urgentPosts"][0]["body"]["sendNow"] is True
    assert publish["urgentToasts"] == ["Duyuru yayımlandı."]


def test_bekleyerek_yayimlanir_toast_ani_soyler(publish: dict[str, Any]) -> None:
    assert "sendNow" not in publish["heldPosts"][0]["body"]
    assert publish["heldToasts"] == ["Duyuru yayımlandı · bildirim ve SMS 07:30'da gidecek."]


def test_varsayilanda_uyari_yok(publish: dict[str, Any]) -> None:
    assert "gece" not in publish["defaultDialog"].lower()
    assert "Yine de şimdi gönder" not in publish["defaultDialog"]
