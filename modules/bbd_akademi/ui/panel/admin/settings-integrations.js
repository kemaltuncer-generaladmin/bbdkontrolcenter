// BBD Akademi — Ayarlar › Entegrasyonlar › YouTube (Akademi README §14 madde 124; F-K9).
//
// YouTube Data API anahtarı oynatma listelerini (liste adı, sıralı videolar, süreler, "Listeyi yenile") açar; tek
// video anahtarsız da çalışır (oEmbed). Öncelik: KM'den girilen KURUM anahtarı → sunucunun PLATFORM anahtarı
// (`AKADEMI_YOUTUBE_API_KEY`) → yok. Kota anahtarın Google projesine işler (günlük 10.000 birim; 200 videoluk liste
// ≈ 9 birim): kurum kendi kotasını isterse buraya girer, istemezse platform anahtarı yeter.
//
// SIR: anahtar yalnız kaydederken gönderilir (`admin_putYoutubeIntegration`, Akademi 1 birimlik istekle doğrular;
// geçersizse `422 youtube_api_key_invalid`); hiçbir yanıtta dönmez — ekranda yalnız son 4 karakter ("…x9Qa").
// Alan kaydedildikten sonra boşaltılır; KM'de hiçbir yere yazılmaz.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../../ui-kit/layout.js';
import { actionButton, confirmAction, fmtDateTime, kv, loader, problem } from '../common.js';

const PATH = '/v1/admin/integrations/youtube';

/** Kaynak → [rozet metni, ton, açıklama]. */
export const YOUTUBE_SOURCES = {
  tenant: ['Kurum anahtarı', 'good', 'Kurumun kendi anahtarı kullanılıyor (kota kurumun Google projesine işler).'],
  platform: ['Platform anahtarı kullanılıyor', 'info', 'Sunucunun ortak anahtarı kullanılıyor. Kurum kendi kotasını '
    + 'isterse aşağıya kendi anahtarını girebilir.'],
  none: ['Anahtar yok', 'warn', 'Oynatma listeleri kapalı: tek video eklenir, liste eklenemez. Anahtarı aşağıya girin.'],
};

export function integrationsTab(app) {
  const node = h('div', 'ak-stack');
  let current = null;
  const view = loader(() => app.api.get(PATH), (data) => {
    current = data;
    return youtubeCard(app, data, (next) => { current = next; view.reload(); });
  }, { rows: 3, columns: 2 });
  node.append(view.node);
  return { node, reload: () => view.reload(), get state() { return current; } };
}

function youtubeCard(app, data, onSaved) {
  const source = YOUTUBE_SOURCES[data?.source] || YOUTUBE_SOURCES.none;
  const body = h('div', 'ak-stack');
  const state = h('div', 'ak-yt-state');
  state.append(badge(source[0], source[1]), data?.playlistsEnabled
    ? badge('Oynatma listeleri açık', 'good') : badge('Oynatma listeleri kapalı', 'warn'));
  body.append(state, h('p', 'ak-muted', source[2]), kv([
    ['Kurum anahtarı', data?.source === 'tenant' && data?.keyHint ? data.keyHint : '—'],
    ['Son değişiklik', data?.updatedAt ? fmtDateTime(data.updatedAt) : '—'],
  ]));

  if (!app.can('admin_putYoutubeIntegration')) {
    body.append(h('small', 'ak-muted', 'Anahtarı değiştirme yetkiniz yok (kurum ayarı yetkisi gerekir).'));
    return card('YouTube', body, 'Oynatma listesi için YouTube Data API anahtarı');
  }

  const input = h('input', 'kit-input ak-yt-key');
  input.type = 'password';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.maxLength = 200;
  input.placeholder = data?.source === 'tenant' ? 'Yeni anahtar (değiştirmek için)' : 'AIza…';
  input.setAttribute('aria-label', 'YouTube Data API anahtarı');
  const error = h('small', 'ak-pl-error');
  error.setAttribute('role', 'alert');
  const save = actionButton(app, 'admin_putYoutubeIntegration', 'Doğrula ve kaydet', { variant: 'primary',
    onClick: async () => {
      error.textContent = '';
      const key = input.value.trim();
      if (key.length < 20) { error.textContent = 'Anahtar en az 20 karakter olmalı.'; return; }
      save.disabled = true;
      save.textContent = 'Doğrulanıyor…';
      try {
        const next = await app.api.put(PATH, { apiKey: key });
        input.value = '';
        app.toast('YouTube anahtarı doğrulandı ve kaydedildi.', 'good');
        onSaved(next);
      } catch (failure) {
        error.textContent = failure?.code === 'youtube_api_key_invalid'
          ? 'Google bu anahtarı kabul etmedi. YouTube Data API v3\'ün açık olduğunu ve anahtarın kısıtlarını denetleyin.'
          : problem(failure);
      } finally {
        save.disabled = false;
        save.textContent = 'Doğrula ve kaydet';
        input.value = '';
      }
    } });
  const row = h('div', 'ak-toolbar');
  row.append(input, save);
  if (data?.source === 'tenant') {
    const remove = button('Kurum anahtarını sil', { variant: 'ghost', onClick: async () => {
      const ok = await confirmAction(app, { title: 'Kurum anahtarı silinsin mi?',
        description: 'Platform anahtarı varsa ona dönülür; yoksa oynatma listeleri kapanır (tek video çalışmaya devam eder).',
        confirmLabel: 'Sil', danger: true });
      if (!ok) return;
      remove.disabled = true;
      try {
        const next = await app.api.put(PATH, { apiKey: null });
        app.toast('Kurum anahtarı silindi.', 'good');
        onSaved(next);
      } catch (failure) {
        error.textContent = problem(failure);
        remove.disabled = false;
      }
    } });
    row.append(remove);
  }
  body.append(row, error, alertBox('Anahtar Google Cloud\'da “YouTube Data API v3” için oluşturulur. Anahtar KM\'ye '
    + 'yazılmaz; Akademi şifreli saklar ve hiçbir ekranda tamamı gösterilmez.', 'info'));
  return card('YouTube', body, 'Oynatma listesi için YouTube Data API anahtarı');
}
