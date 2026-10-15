// BBD Akademi — ANA SAYFA (15.10.2026, kullanıcı: "sadeleştirip kolay kullanım,
// full görsellik").
//
// Panel buradan açılır. Üç parça:
//   · Hızlı işler — günün en sık işleri tek tıkta (yoklama al, ödev ver, görev
//     ata, duyuru yaz, tahsilat al, öğrenci bul); yalnız görünür ekranlar.
//   · Kutucuklar — her ana grup bir kutucuk (`sections.js` → `homeTiles`):
//     simge, ad, ne işe yaradığı, günün sayısı ve grubun BÜTÜN ekranları. Her
//     ekran buradan TEK tıktadır; kutucuğun adı grubun ilk ekranını açar.
//     Koçluk kutucuğu öne çıkar (lider 15.10.2026: koçluk kullanıcı için çok
//     önemli) — geniş ve vurgulu.
//   · Günün sayıları — kutucuklardaki rakamlar "Bugün" ekranıyla AYNI özet
//     yükünden (`admin_getDashboard`, 60 sn önbellekli; tek istek). Okunamazsa
//     ya da yetki yoksa rakam çizilmez, ana sayfa yine açılır (rakam süstür,
//     kapı değildir).

import { button, h, loadStyles, money } from '../../ui-kit/kit.js';
import { num, sectionHead } from './common.js';
import { navIcon } from './nav-icons.js';
import { resolveTarget } from './sections.js';

/** Hızlı işler: [etiket, hedef ekran, ek veri]. Görünmeyen hedefin düğmesi çizilmez. */
const QUICK = [
  ['Yoklama al', 'attendance'],
  ['Ödev ver', 'homework'],
  ['Görev ata', 'coachWeek'],
  ['Kontrol kuyruğu', 'coachQueue'],
  ['Duyuru yaz', 'announcements'],
  ['Tahsilat al', 'finance'],
  ['Öğrenci bul', 'students'],
];

/** Öne çıkan gruplar (geniş kutucuk). */
const FEATURED = new Set(['coaching']);

/**
 * Grup başına günün sayısı (`admin_getDashboard` yükünden). Dönen:
 * {text, tone, title} ya da null (sayı yok → çizilmez).
 */
export function tileStat(key, data, { has = () => true } = {}) {
  if (!data || typeof data !== 'object') return null;
  const pending = data.pendingRequests || {};
  const pendingTotal = (pending.leave || 0) + (pending.meeting || 0) + (pending.etut || 0);
  const attendance = data.attendance || {};
  switch (key) {
    case 'today':
      return { text: `${num(pendingTotal)} bekleyen talep`, tone: pendingTotal ? 'warn' : 'good',
        title: `İzin ${num(pending.leave || 0)} · Görüşme ${num(pending.meeting || 0)} · Etüt ${num(pending.etut || 0)}` };
    case 'students':
      return data.presence ? { text: `${num(data.presence.inInstitution || 0)} / ${num(data.presence.activeStudents || 0)} kurumda`,
        tone: '', title: 'Bugün kantin harcaması ya da tablet oturumu olan öğrenciler.' } : null;
    case 'coaching': {
      const coaching = data.coaching;
      if (!coaching) return null;
      const busy = (coaching.openQuestions || 0) + (coaching.pendingReviews || 0);
      return { text: `${num(coaching.openQuestions || 0)} soru · ${num(coaching.pendingReviews || 0)} kontrol`,
        tone: busy ? 'warn' : 'good', title: 'Cevapsız hoca sorusu · koçun kontrolünü bekleyen teslim' };
    }
    case 'lessons':
      return data.lessons ? { text: `${num(data.lessons.ongoing || 0)} ders sürüyor / ${num(data.lessons.scheduled || 0)}`,
        tone: '', title: 'Bugünkü dersler' } : null;
    case 'attendance': {
      const total = attendance.sessionsTotal || 0;
      const taken = attendance.sessionsTaken || 0;
      if (!total) return null;
      return { text: `${num(taken)}/${num(total)} yoklama alındı`, tone: taken < total ? 'warn' : 'good',
        title: `${num(attendance.absentCount || 0)} öğrenci gelmedi.` };
    }
    case 'money': {
      const finance = data.institutionFinance;
      if (has('finance') && finance) {
        return { text: `Gecikmiş ${money(finance.overdueMinor || 0)}`, tone: finance.overdueMinor ? 'bad' : 'good',
          title: `${num(finance.overdueStudentCount || 0)} öğrencide gecikme · bugün tahsil ${money(finance.collectedTodayMinor || 0)}` };
      }
      return data.pendingBookRequests ? { text: `${num(data.pendingBookRequests)} kitap talebi`, tone: 'warn',
        title: 'Bekleyen kitap talepleri' } : null;
    }
    case 'reports': {
      const warnings = data.earlyWarnings;
      if (!warnings) return null;
      return { text: `${num(warnings.open || 0)} açık erken uyarı`, tone: warnings.high ? 'bad' : (warnings.open ? 'warn' : 'good'),
        title: warnings.high ? `${num(warnings.high)} yüksek önemli` : 'Açık uyarılar' };
    }
    default:
      return null;
  }
}

const today = () => new Date().toLocaleDateString('tr-TR', { timeZone: 'Europe/Istanbul', weekday: 'long',
  day: 'numeric', month: 'long', year: 'numeric' });

/**
 * @param app  panel bağlamı (`go`, `can`, `has`, `api`)
 * @param {{tiles: () => object[], nav: () => object[]}} source  güncel kutucuklar ve gezinme
 * @returns {{node: HTMLElement, shown: (options?: {fresh?: boolean}) => void}}
 */
export function homeView(app, { tiles, nav }) {
  loadStyles(import.meta.url, 'home.css');
  const node = h('div', 'ak-section ak-home');
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => shown({ fresh: true }) });
  const head = sectionHead('BBD Akademi', { subtitle: today(), actions: [refresh] });
  const quick = h('div', 'ak-home-quick');
  quick.setAttribute('role', 'group');
  quick.setAttribute('aria-label', 'Hızlı işler');
  const grid = h('div', 'ak-home-grid');
  const status = h('p', 'ak-home-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  node.append(head, quick, grid, status);

  let stats = null;                   // son okunan özet (null = yok / okunamadı)
  let loadedAt = 0;

  function paintQuick() {
    quick.replaceChildren(h('span', 'ak-home-quick-label', 'Hızlı işler'));
    const menu = nav();
    for (const [label, target, payload] of QUICK) {
      if (!resolveTarget(menu, target, payload || null)) continue;
      const item = button(label, { variant: target === 'coachWeek' ? 'primary' : '',
        onClick: () => app.go(target, payload || null) });
      quick.append(item);
    }
    quick.hidden = quick.children.length < 2;
  }

  function tile(spec) {
    const card = h('article', `ak-tile ak-tone-${spec.tone || 'slate'}${FEATURED.has(spec.key) ? ' ak-tile-feature' : ''}`);
    const titleId = `ak-tile-${spec.key}`;
    card.setAttribute('aria-labelledby', titleId);
    const topRow = h('div', 'ak-tile-top');
    const icon = h('span', 'ak-tile-icon');
    icon.append(navIcon(spec.icon));
    topRow.append(icon);
    const stat = tileStat(spec.key, stats, { has: (permission) => app.has(permission) });
    if (stat) {
      const pill = h('span', `ak-tile-stat${stat.tone ? ` ${stat.tone}` : ''}`, stat.text);
      if (stat.title) pill.title = stat.title;
      topRow.append(pill);
    }
    const title = h('h3', 'ak-tile-title');
    const open = h('button', 'ak-tile-open', spec.label);
    open.type = 'button';
    open.id = titleId;
    open.addEventListener('click', () => app.go(spec.key));
    title.append(open);
    const hint = h('p', 'ak-tile-hint', spec.hint || '');
    const links = h('ul', 'ak-tile-links');
    links.setAttribute('aria-label', `${spec.label} ekranları`);
    for (const link of spec.links) {
      const li = h('li');
      const go = h('button', 'ak-tile-link', link.label);
      go.type = 'button';
      go.addEventListener('click', () => app.go(link.key));
      li.append(go);
      links.append(li);
    }
    card.append(topRow, title, hint, links);
    return card;
  }

  function paint() {
    head.querySelector('.ak-sub')?.replaceChildren(document.createTextNode(
      `${today()} · Bir bölüm seçin; her ekran kutucuğunun altında, tek tıkta.`));
    paintQuick();
    grid.replaceChildren(...tiles().map(tile));
  }

  async function loadStats(fresh) {
    if (!app.can('admin_getDashboard')) return;
    if (!fresh && stats && Date.now() - loadedAt < 60_000) return;
    try {
      if (fresh) app.api.invalidate('/v1/admin/dashboard');
      stats = await app.api.getCached('/v1/admin/dashboard');
      loadedAt = Date.now();
      status.textContent = '';
    } catch {
      // Rakam süstür: okunamazsa kutucuklar sayısız çizilir, ana sayfa açık kalır.
      stats = null;
      status.textContent = 'Günün sayıları şu an okunamadı; bölümler yine açılır.';
    }
    paint();
  }

  function shown({ fresh = false } = {}) {
    paint();
    loadStats(fresh);
  }

  return { node, shown };
}
