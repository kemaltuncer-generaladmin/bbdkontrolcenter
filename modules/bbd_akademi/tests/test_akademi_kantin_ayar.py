"""Kantin modülü kurum anahtarı (iş 43-kantin-ayar) — Ayarlar → Özellikler.

Kullanıcı: "kantin velide gözükmesin, kantini açma kapama ayarı ekleyelim kuruma göre".

- "Kantin (veli ve öğrenci uygulamasında)" satırı anlaşılır: kapalıyken veli/öğrencinin kantini görmediği,
  kantin kasası ve KM kantin modülünün etkilenmediği yazar.
- Kapatırken onay penceresi kantine özgü açıklamayı gösterir; onaylanınca `admin_patchFeature`
  `{enabled: false}` gider ve satır "Kapalı" olur.
"""

from __future__ import annotations

from typing import Any

from modules.bbd_akademi.tests.test_akademi_ogretmen_koc import (  # ortak taklit Akademi + panel
    PRELUDE,
    node_run,
)

FEATURES = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  let canteen = { key: 'canteen', enabled: true, config: null, editable: true };
  routes['GET /v1/admin/features'] = () => ({ items: [canteen,
    { key: 'finance', enabled: true, config: null, editable: true }] });
  routes['PATCH /v1/admin/features/canteen'] = (body) => { canteen = { ...canteen, ...body }; return canteen; };
  const { app, root } = await makeApp(load, document);
  const { section } = await load('./panels/bbd_akademi/settings.js');
  const panel = section(app);
  root.append(panel.node);
  panel.shown({ tab: 'features' });
  await sleep(40);
  const out = {};
  const row = () => root.querySelectorAll('.ak-set-flag').find((n) => n.textContent.includes('Kantin (veli'));
  out.rowText = row()?.textContent || '';
  out.a11y = audit(row());
  const toggle = row().querySelector('input');
  toggle.checked = false;
  toggle.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(20);
  const dialog = root.querySelector('.kit-dialog');
  out.dialogText = dialog?.textContent || '';
  click(byText(dialog, 'button', 'Kapat'));
  await sleep(60);
  out.patches = sent.filter((c) => c.method === 'PATCH').map((c) => [c.path, c.body]);
  out.after = row()?.textContent || '';
  return out;
}
"""


def test_ozellikler_kantin_anahtari_anlasilir_ve_kapatilir() -> None:
    out: dict[str, Any] = node_run(FEATURES)
    assert "Kantin (veli ve öğrenci uygulamasında)" in out["rowText"]
    assert "Kapalıyken veli ve öğrenci kantini HİÇ görmez" in out["rowText"]
    assert "Kantin kasası ve Kontrol Merkezi kantin modülü etkilenmez" in out["rowText"]
    assert out["a11y"] == []
    assert "kapatılsın mı" in out["dialogText"]
    assert "Kantin kasası ve Kontrol Merkezi kantin modülü çalışmaya devam eder" in out["dialogText"]
    assert out["patches"] == [["/v1/admin/features/canteen", {"enabled": False}]]
    assert "Kapalı" in out["after"]
