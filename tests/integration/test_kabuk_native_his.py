"""KM'ye gerçek masaüstü uygulaması hissi (ADR 0032) — kabuk ve ui-kit.

Kullanıcı (16.10.2026): "km yi gerçek native uygulama hissiyatına kavuşturalım".

Sınananlar (Node, `tools/a11y/harness.py`; `node` yoksa atlanır):

* `ui-kernel.js` istek katmanı: GET önbelleği, ekran açılışında son bilinen
  yanıt + arkada tazeleme (değişince `onStale`), yazma önbelleği boşaltır,
  bağlantı yokken okuma son bilinen yanıttan gelir, yazma AÇIK hata verir;
  istek sunucuya hiç ulaşmadıysa "Yeniden dene" sorulur, zaman aşımında
  sorulmaz (para/yoklama: çift kayıt olmasın), oturum kapanınca önbellek boşalır.
* `native.js`: kısayol tablosu (macOS ⌘ / Windows Ctrl, Türkçe klavye),
  PIN rakamları ve Ctrl+C kısayola dönüşmez, bağlam menüsü satırları (tablo
  satırı, yazı alanı, seçili metin, panelin eklediği satır), CSV, aynı komutun
  iki yoldan gelmesi (menü + tuş) tek çalışır.
* `ui-kit/select.js`: bütün tekli `<select>`lar aranabilir listeyle açılır,
  `change` olayı ve değer yerel kutudaki gibi; Esc değiştirmeden kapatır.
* `findbar.js`: Türkçe harf katlamalı arama.

Statik: Tauri ayarı (pencere kodda kurulur), yeni eklentiler ilan edilmiş ve
JS'e hiçbir yeni izin açılmamış, menü çubuğundaki her komutun arayüzde bir
karşılığı var, geçişler ≤ 150 ms.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

DESKTOP = ROOT / "apps" / "desktop"
SHELL = DESKTOP / "shell"
TAURI = DESKTOP / "src-tauri"


def node_run(scenario: str) -> dict:
    try:
        data = run(scenario)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    return data["result"]


# --------------------------------------------------------------- istek katmanı

KERNEL = r"""
export default async function ({ load, sleep, document }) {
  const calls = [];
  let mode = 'ok';
  let version = 1;
  window.__TAURI__ = { core: { invoke: async (cmd, args) => {
    if (cmd !== 'core_request') return null;
    calls.push(`${args.method} ${args.path}`);
    if (mode === 'connect') throw '[connect] çekirdeğe bağlanılamadı: dns';
    if (mode === 'timeout') throw '[timeout] çekirdeğe bağlanılamadı: operation timed out';
    if (args.path === '/health') return { status: 200, body: '{"status":"ok"}' };
    if (args.method === 'GET') return { status: 200, body: JSON.stringify({ v: version }) };
    return { status: 200, body: '{"ok":true}' };
  } } };
  const k = await load('./ui-kernel.js');
  const out = {};
  const count = () => calls.length;

  out.first = (await k.api('/api/x')).v;
  // Açılış kapsamı DIŞINDA: her okuma ağa gider (yoklama/canlı veri bozulmaz).
  let n = count();
  out.outsideScope = (await k.api('/api/x')).v;
  out.outsideScopeNetwork = count() - n;

  // Ekran açılışı: son bilinen yanıt HEMEN, gerçeği arkadan.
  version = 2;
  let stale = 0;
  let settled = null;
  const root = document.createElement('div');
  document.body.append(root);
  await k.mountPanel({ id: 'p', title: 'P', entry: null }, root, { panels: [] }, null,
    { onStale: () => { stale += 1; }, onSettled: (r) => { settled = r; } });
  n = count();
  out.swr = (await k.api('/api/x')).v;
  await sleep(5);
  out.revalidated = count() - n;
  out.stale = stale;
  out.afterRevalidate = (await k.api('/api/x')).v;
  await sleep(5);
  out.settled = settled !== null;

  // Yazma önbelleği boşaltır.
  await k.api('/api/x', { method: 'POST', body: {} });
  n = count();
  out.afterWrite = (await k.api('/api/x')).v;
  out.afterWriteNetwork = count() - n;

  // "Yenile": bir sonraki açılış önbelleği kullanmaz.
  k.bypassCacheOnce();
  await k.mountPanel({ id: 'p', title: 'P', entry: null }, root, { panels: [] }, null, {});
  n = count();
  await k.api('/api/x');
  out.bypassNetwork = count() - n;

  // Bağlantı yok: okuma son bilinen yanıttan, uygulama durmaz.
  mode = 'connect';
  out.offlineRead = (await k.api('/api/x', { fresh: true })).v;
  await sleep(5);
  out.online = k.connection.online;
  out.banner = document.querySelector('.km-conn')?.textContent || '';
  try { await k.api('/api/hic-yuklenmedi'); out.noCache = 'served'; }
  catch (error) { out.noCache = error.message; out.noCacheOffline = error.offline === true; }

  // Yazma, istek HİÇ GİTMEDİ: açıkça sorulur; "Yeniden dene" aynı isteği yollar.
  const pending = k.api('/api/kaydet', { method: 'POST', body: { a: 1 } })
    .then((value) => ({ ok: value.ok }), (error) => ({ err: error.message }));
  await sleep(5);
  const dialog = document.querySelector('.km-modal[role="alertdialog"]');
  out.dialog = Boolean(dialog);
  out.dialogText = dialog?.textContent || '';
  mode = 'ok';
  document.querySelector('.km-modal .km-btn-primary').click();
  out.retried = await pending;
  out.dialogClosed = !document.querySelector('.km-modal');

  // Yazma, istek GİTMİŞ OLABİLİR (zaman aşımı): yeniden deneme ÖNERİLMEZ.
  mode = 'timeout';
  try { await k.api('/api/kaydet', { method: 'POST', body: {} }); out.timeout = 'ok'; }
  catch (error) { out.timeout = error.message; }
  out.dialogOnTimeout = Boolean(document.querySelector('.km-modal'));

  // Vazgeç: işlem yapılmadığı açıkça söylenir.
  mode = 'connect';
  const cancelled = k.api('/api/kaydet', { method: 'POST', body: {} }).then(() => 'ok', (error) => error.message);
  await sleep(5);
  document.querySelector('.km-modal .km-btn:not(.km-btn-primary)').click();
  out.cancelled = await cancelled;

  // Oturum kapanınca önceki kişinin yanıtları bellekte kalmaz.
  k.setToken(null);
  try { await k.api('/api/x'); out.afterLogout = 'served'; }
  catch (error) { out.afterLogout = error.offline ? 'offline-error' : error.message; }

  // Kimlik/eşleme uçları kendi hata ekranını kurar: pencere açılmaz.
  try { await k.api('/api/auth/login', { method: 'POST', body: {} }); }
  catch (error) { out.loginError = String(error.message || error); }
  out.loginDialog = Boolean(document.querySelector('.km-modal'));
  return out;
}
"""


def test_istek_katmani_onbellek_ve_baglanti() -> None:
    r = node_run(KERNEL)
    assert r["first"] == 1
    assert r["outsideScope"] == 1 and r["outsideScopeNetwork"] == 1
    # Açılışta son bilinen yanıt (1) anında, arkada tazeleme (1 istek), değişti → onStale.
    assert r["swr"] == 1 and r["revalidated"] == 1 and r["stale"] == 1
    assert r["afterRevalidate"] == 2 and r["settled"] is True
    assert r["afterWrite"] == 2 and r["afterWriteNetwork"] == 1
    assert r["bypassNetwork"] == 1
    assert r["offlineRead"] == 2 and r["online"] is False
    assert "Bağlantı yok" in r["banner"]
    assert "daha önce yüklenmediği" in r["noCache"] and r["noCacheOffline"] is True
    assert r["dialog"] is True and "HİÇBİR ŞEY KAYDEDİLMEDİ" in r["dialogText"]
    assert r["retried"] == {"ok": True} and r["dialogClosed"] is True
    assert "kaydedilmiş OLABİLİR" in r["timeout"] and r["dialogOnTimeout"] is False
    assert "YAPILMADI" in r["cancelled"]
    assert r["afterLogout"] == "offline-error"
    assert r["loginError"].startswith("[connect]") and r["loginDialog"] is False


NET = r"""
export default async function ({ load }) {
  const n = await load('./net.js');
  let now = 0;
  const cache = n.createResponseCache({ maxEntries: 2, maxChars: 400, now: () => now });
  cache.set('a', 'x'.repeat(10)); now = 5; cache.set('b', 'y'); cache.get('a'); cache.set('c', 'z');
  const lru = ['a', 'b', 'c'].map((key) => Boolean(cache.get(key)));
  cache.set('big', 'q'.repeat(150));
  return {
    lru, big: cache.get('big'), age: cache.get('a').age,
    cacheable: ['/api/students', '/health', '/modules', '/api/auth/me', '/api/pairing/state',
      '/api/reports/x/export', '/api/x/preview?y=1', '/api/x/run'].map((p) => n.cacheable('GET', p)),
    post: n.cacheable('POST', '/api/students'),
    kinds: ['[connect] a', '[timeout] b', '[network] c', 'İstek başarısız (500).'].map((m) => n.failureKind(new Error(m))),
    fetchKind: n.failureKind(Object.assign(new Error('Failed to fetch'), { name: 'TypeError' })),
    abortKind: n.failureKind(Object.assign(new Error('aborted'), { name: 'AbortError' })),
    gateway: [502, 503, 504, 500, 404].map(n.isGateway),
  };
}
"""


def test_net_saf_mantik() -> None:
    r = node_run(NET)
    assert r["lru"] == [True, False, True]          # en uzun süre okunmayan düştü
    assert r["big"] is None                          # bütçenin dörtte birinden büyük saklanmaz
    assert r["age"] == 5                             # yaş: yazıldığı andan bu yana
    assert r["cacheable"] == [True, False, False, False, False, False, False, False]
    assert r["post"] is False
    assert r["kinds"] == ["connect", "timeout", "network", None]
    assert r["fetchKind"] == "connect" and r["abortKind"] == "timeout"
    assert r["gateway"] == [True, True, True, False, False]


# ------------------------------------------------------------- kısayol ve menü

NATIVE = r"""
export default async function ({ load, document }) {
  const n = await load('./native.js');
  const K = (init) => ({ key: '', code: '', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init });
  const win = (init) => n.matchShortcut(K(init), false);
  const mac = (init) => n.matchShortcut(K(init), true);
  const keys = {
    winSearch: win({ ctrlKey: true, code: 'KeyK', key: 'k' }),
    macSearch: mac({ metaKey: true, code: 'KeyK', key: 'k' }),
    macCtrlK: mac({ ctrlKey: true, code: 'KeyK', key: 'k' }),
    winMetaK: win({ metaKey: true, code: 'KeyK', key: 'k' }),
    trBack: win({ ctrlKey: true, code: 'BracketLeft', key: 'ğ' }),
    trForward: win({ ctrlKey: true, code: 'BracketRight', key: 'ü' }),
    go3: win({ ctrlKey: true, code: 'Digit3', key: '3' }),
    trZoomOut: win({ ctrlKey: true, code: 'Equal', key: '-' }),
    zoomIn: win({ ctrlKey: true, code: 'Equal', key: '=' }),
    zoomReset: win({ ctrlKey: true, code: 'Digit0', key: '0' }),
    f5: win({ code: 'F5', key: 'F5' }),
    f11win: win({ code: 'F11', key: 'F11' }),
    f11mac: mac({ code: 'F11', key: 'F11' }),
    macFull: mac({ metaKey: true, ctrlKey: true, code: 'KeyF', key: 'f' }),
    findNext: win({ ctrlKey: true, code: 'KeyG', key: 'g' }),
    findPrev: win({ ctrlKey: true, shiftKey: true, code: 'KeyG', key: 'G' }),
    altLeft: win({ altKey: true, code: 'ArrowLeft', key: 'ArrowLeft' }),
    prefs: mac({ metaKey: true, code: 'Comma', key: ',' }),
    print: win({ ctrlKey: true, code: 'KeyP', key: 'p' }),
    pinDigit: win({ code: 'Digit4', key: '4' }),
    copy: win({ ctrlKey: true, code: 'KeyC', key: 'c' }),
    paste: mac({ metaKey: true, code: 'KeyV', key: 'v' }),
    altGr: win({ ctrlKey: true, altKey: true, code: 'KeyQ', key: '@' }),
  };

  // Aynı komut iki yoldan (menü hızlandırıcısı + sayfa tuşu) gelirse BİR kez.
  let ran = 0;
  n.defineCommand('deneme', () => { ran += 1; });
  n.runCommand('deneme', 'menu');
  n.runCommand('deneme', 'key');
  const deduped = ran;
  n.runCommand('deneme', 'key');
  let gated = 0;
  n.defineCommand('kapali', () => { gated += 1; }, { when: () => false });
  n.runCommand('kapali', 'key');

  // Tablo satırı bağlam menüsü.
  const table = document.createElement('div');
  table.setAttribute('role', 'table');
  const row = (cells, head = false) => {
    const r = document.createElement('div');
    r.setAttribute('role', 'row');
    r.className = head ? 'kit-row kit-head' : 'kit-row';
    for (const text of cells) {
      const c = document.createElement('span');
      c.className = 'kit-cell';
      c.textContent = text;
      r.append(c);
    }
    table.append(r);
    return r;
  };
  row(['Ad', 'Sınıf', 'Veli telefonu'], true);
  const first = row(['Elif Kaya', '9-A', '0532 111 22 33']);
  row(['Ömer "Can" Şahin', '10-B', '']);
  document.body.append(table);
  let opened = 0;
  first.addEventListener('click', () => { opened += 1; });
  table.addEventListener('km-contextmenu', (event) => event.detail.add({ label: 'Yoklamayı aç', run: () => {} }));
  const rowItems = n.buildContextItems(first.children[1], { selection: '' });
  rowItems.find((item) => item.id === 'open-row').run();

  const input = document.createElement('input');
  document.body.append(input);
  const inputItems = n.buildContextItems(input, { selection: '' }).filter((i) => !i.separator).map((i) => [i.id, i.enabled !== false]);
  let searched = null;
  const textItems = n.buildContextItems(document.body, { selection: 'Elif Kaya', command: (id, _src, arg) => { searched = [id, arg]; } });
  textItems.find((item) => item.id === 'search-selection').run();

  return {
    keys, deduped, ranAfter: ran, gated,
    rowIds: rowItems.filter((i) => !i.separator).map((i) => i.id),
    rowLabel: rowItems.find((i) => i.id === 'panel-0')?.label,
    opened,
    rowText: n.rowText(first),
    csv: n.tableCsv(table),
    inputItems,
    textIds: textItems.filter((i) => !i.separator).map((i) => i.id).slice(0, 2),
    searched,
    label: [n.keyLabel('Mod+K'), n.keyLabel('Mod+Shift+G')],
  };
}
"""


def test_kisayollar_ve_baglam_menusu() -> None:
    r = node_run(NATIVE)
    k = r["keys"]
    assert k["winSearch"] == "search" and k["macSearch"] == "search"
    assert k["macCtrlK"] is None and k["winMetaK"] is None
    # Türkçe Q klavye: Ctrl+Ğ/Ü geri/ileri (fiziksel tuş), Ctrl+"-" uzaklaştır (karakter).
    assert k["trBack"] == "back" and k["trForward"] == "forward"
    assert k["go3"] == "go:3"
    assert k["trZoomOut"] == "zoom-out" and k["zoomIn"] == "zoom-in" and k["zoomReset"] == "zoom-reset"
    assert k["f5"] == "refresh" and k["f11win"] == "fullscreen" and k["f11mac"] is None
    assert k["macFull"] == "fullscreen"
    assert k["findNext"] == "find-next" and k["findPrev"] == "find-prev"
    assert k["altLeft"] == "back" and k["prefs"] == "prefs" and k["print"] == "print"
    # PIN tuş takımı, kopyala/yapıştır ve AltGr karakterleri kısayola dönüşmez.
    assert k["pinDigit"] is None and k["copy"] is None and k["paste"] is None and k["altGr"] is None

    assert r["deduped"] == 1 and r["ranAfter"] == 2 and r["gated"] == 0

    assert r["rowIds"][:4] == ["copy-row", "copy-cell", "open-row", "export-table"]
    assert "panel-0" in r["rowIds"] and r["rowLabel"] == "Yoklamayı aç"
    assert {"back", "forward", "refresh", "find", "print"} <= set(r["rowIds"])
    assert r["opened"] == 1
    assert r["rowText"] == "Elif Kaya\t9-A\t0532 111 22 33"
    assert r["csv"].split("\r\n") == [
        '"Ad";"Sınıf";"Veli telefonu"',
        '"Elif Kaya";"9-A";"0532 111 22 33"',
        '"Ömer ""Can"" Şahin";"10-B";""',
    ]
    ids = dict(r["inputItems"])
    assert list(ids) == ["undo", "redo", "cut", "copy", "paste", "select-all"]
    assert ids["cut"] is False and ids["copy"] is False and ids["paste"] is True  # seçim yokken kes/kopyala pasif
    assert r["textIds"] == ["copy", "search-selection"] and r["searched"] == ["search-text", "Elif Kaya"]
    assert r["label"] == ["Ctrl+K", "Ctrl+Shift+G"]


# ----------------------------------------------------------- aranabilir liste

SELECT = r"""
export default async function ({ load, sleep, document, audit }) {
  const s = await load('./ui-kit/select.js');
  s.enhanceSelects(document);
  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Sınıf');
  const add = (parent, value, label, disabled = false) => {
    const o = document.createElement('option');
    o.value = value; o.textContent = label; o.disabled = disabled;
    parent.append(o);
  };
  add(select, '', 'Sınıf seçin');
  const group = document.createElement('optgroup');
  group.label = 'Lise';
  for (const n of ['9-A', '9-B', '10-A', '10-B', '11-A']) add(group, n, `${n} sınıfı`);
  add(group, 'kapali', 'Kapalı şube', true);
  select.append(group);
  for (const n of ['Işık', 'Öğretmenler', 'Şube Ç']) add(select, n, n);
  const changes = [];
  select.addEventListener('change', () => changes.push(select.value));
  document.body.append(select);

  select.dispatchEvent(new MouseEvent('mousedown', { button: 0 }));
  const pop = () => document.querySelector('.kit-lb');
  const out = { opened: Boolean(pop()), expanded: select.getAttribute('aria-expanded') };
  const search = pop().querySelector('.kit-lb-search');
  out.search = Boolean(search);
  out.groups = [...pop().querySelectorAll('.kit-lb-group')].map((g) => g.textContent);
  out.violations = audit(pop());
  search.value = 'isik';
  search.dispatchEvent(new Event('input'));
  out.filtered = [...pop().querySelectorAll('.kit-lb-opt')].map((o) => o.textContent);
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  out.value = select.value;
  out.changes = [...changes];
  out.closed = !pop();

  // Klavye: Boşluk açar, ↓ gezer (kapalı şube atlanır), Esc DEĞİŞTİRMEDEN kapatır.
  select.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
  out.reopened = Boolean(pop());
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  const list = pop()?.querySelector('.kit-lb-search');
  list?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  out.escClosed = !pop();
  out.escValue = select.value;

  select.selectedIndex = 5;   // 11-A
  select.dispatchEvent(new MouseEvent('mousedown', { button: 0 }));
  const box = pop();
  const active = () => box.querySelector('.kit-lb-opt.active')?.textContent;
  out.activeStart = active();
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }));
  out.skipDisabled = active();
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  out.picked = select.value;

  // Çoklu ve devre dışı kutuya dokunulmaz.
  const multi = document.createElement('select');
  multi.multiple = true;
  add(multi, 'a', 'A');
  document.body.append(multi);
  multi.dispatchEvent(new MouseEvent('mousedown', { button: 0 }));
  out.multiUntouched = !pop();

  // Hazır bileşen.
  const field = s.kitSelect({ label: 'Dönem', options: ['Güz', { value: 'bahar', label: 'Bahar' }], value: 'bahar',
    onChange: (v) => { out.componentChange = v; } });
  document.body.append(field.node);
  out.component = [field.value, field.node.textContent.startsWith('Dönem')];
  field.select.dispatchEvent(new MouseEvent('mousedown', { button: 0 }));
  out.shortNoSearch = !pop()?.querySelector('.kit-lb-search');
  pop().dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
  pop().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  return out;
}
"""


def test_aranabilir_acilir_liste() -> None:
    r = node_run(SELECT)
    assert r["opened"] is True and r["expanded"] == "true"
    assert r["search"] is True and r["groups"] == ["Lise"]
    assert r["violations"] == [], r["violations"]
    assert r["filtered"] == ["Işık"]                       # Türkçe katlama
    assert r["value"] == "Işık" and r["changes"] == ["Işık"] and r["closed"] is True
    assert r["reopened"] is True and r["escClosed"] is True and r["escValue"] == "Işık"
    assert r["activeStart"] == "11-A sınıfı" and r["skipDisabled"] == "Işık"
    assert r["picked"] == "Işık"
    assert r["multiUntouched"] is True
    assert r["component"] == ["bahar", True]
    assert r["shortNoSearch"] is True and r["componentChange"] == "Güz"


FIND = r"""
export default async function ({ load, document }) {
  const f = await load('./findbar.js');
  const root = document.createElement('div');
  root.innerHTML = '';
  const p1 = document.createElement('p'); p1.textContent = 'Öğrenci listesi — ÖĞRENCİ sayısı';
  const p2 = document.createElement('p'); p2.textContent = 'IŞIK sınıfı';
  const hidden = document.createElement('p'); hidden.hidden = true; hidden.textContent = 'öğrenci gizli';
  root.append(p1, p2, hidden);
  document.body.append(root);
  return {
    ogr: f.findMatches(root, 'ogrenci').map((m) => [m.start, m.end]),
    isik: f.findMatches(root, 'isik').length,
    empty: f.findMatches(root, '  ').length,
    keep: [f.foldKeep('İstanbul').length, f.foldKeep('IŞIK')],
  };
}
"""


def test_sayfada_bul_turkce() -> None:
    r = node_run(FIND)
    assert r["ogr"] == [[0, 7], [18, 25]]     # gizli paragraf aranmaz
    assert r["isik"] == 1 and r["empty"] == 0
    assert r["keep"] == [8, "isik"]


# --------------------------------------------------------------------- statik

def test_tauri_pencere_kodda_eklentiler_ilan_izin_yok() -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    # Pencere platforma göre seçenekle kodda kurulur (src/native.rs).
    assert conf["app"]["windows"] == []
    cargo = (TAURI / "Cargo.toml").read_text(encoding="utf-8")
    for plugin in ("tauri-plugin-window-state", "tauri-plugin-single-instance",
                   "tauri-plugin-notification", "tauri-plugin-dialog"):
        assert re.search(rf'^{plugin} = "2"$', cargo, re.MULTILINE), plugin
    # En az izin: eklentilerin JS komutlarına hiçbir izin açılmadı; kabuk
    # onlara kendi komutlarıyla ulaşır.
    perms = []
    for path in sorted((TAURI / "capabilities").glob("*.json")):
        perms += json.loads(path.read_text(encoding="utf-8")).get("permissions", [])
    assert perms == ["updater:default"]
    main = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    # Tek örnek eklentisi İLK eklentidir (ikinci açılış başka hiçbir şey kurmadan biter).
    plugins = re.findall(r"\.plugin\(\s*(tauri_plugin_\w+)", main)
    assert plugins[0] == "tauri_plugin_single_instance", plugins
    # Görünürlük geri yüklenmez: pencere ilk boyaya kadar gizli.
    assert "StateFlags::VISIBLE" in main and "!tauri_plugin_window_state::StateFlags::VISIBLE" in main


def test_menu_cubugundaki_her_komutun_arayuzde_karsiligi_var() -> None:
    rust = (TAURI / "src" / "native.rs").read_text(encoding="utf-8")
    ids = set(re.findall(r'item\(\s*app,\s*"([a-z:\-]+)"', rust)) | set(re.findall(r'check\(app, "([a-z:\-]+)"', rust))
    ids |= {f"go:{n}" for n in range(1, 10)}
    ids -= {"go:none", "quit"}  # bilgi satırı / Rust'ta işlenir
    app = (SHELL / "app.js").read_text(encoding="utf-8")
    defined = set(re.findall(r"defineCommand\('([a-z:\-]+)'", app))
    defined |= {f"go:{n}" for n in range(1, 10)} if "defineCommand(`go:${n}`" in app else set()
    defined |= {f"theme:{t}" for t in ("system", "light", "dark")} if "defineCommand(`theme:${theme}`" in app else set()
    defined |= {f"density:{d}" for d in ("comfortable", "compact")} if "defineCommand(`density:${density}`" in app else set()
    missing = sorted(ids - defined)
    assert missing == [], missing


def _durations(css: str) -> list[tuple[str, int]]:
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    body = re.sub(r"@media \(prefers-reduced-motion[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", body)
    out = []
    for rule in body.split("}"):
        decl = rule.split("{")[-1]
        for prop in re.findall(r"(?:transition|animation)\s*:\s*([^;]+)", decl):
            if "infinite" in prop:
                continue   # sürekli gösterge (yükleniyor ışıltısı, logo halkası) — geçiş değil
            for value, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s)\b", prop):
                ms = float(value) * (1000 if unit == "s" else 1)
                out.append((prop.strip(), int(ms)))
    return out


def test_gecisler_kisa_ve_hareket_azaltmaya_uyar() -> None:
    for path in (SHELL / "style.css", SHELL / "ui-kit" / "kit.css"):
        css = path.read_text(encoding="utf-8")
        long = [item for item in _durations(css) if item[1] > 150]
        assert long == [], (path.name, long)
    style = (SHELL / "style.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in style
    # Tema: açık varsayılan, koyu aynı jetonları ezer; OS teması `boot.js`ten.
    assert ':root[data-theme="dark"]' in style and "color-scheme: dark" in style
    boot = (SHELL / "boot.js").read_text(encoding="utf-8")
    assert "prefers-color-scheme: dark" in boot
    index = (SHELL / "index.html").read_text(encoding="utf-8")
    assert index.index('src="boot.js"') < index.index('href="style.css"') < index.index('src="app.js"')
