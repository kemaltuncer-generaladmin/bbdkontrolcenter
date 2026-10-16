// BBD Akademi — koçluk metin şablonlarının SAF mantığı (Akademi README §14 madde 115; Node ile sınanır).
//
// Kullanıcı (16.10.2026): "koçluk kısmına şablon ekleyelim". Koçun her gün yeniden yazdığı metinler —
// görüşme notu (hazır eylem maddeleri + varsayılan görünürlük), soru yanıtı, check-in yorumu, veli görüşmesi,
// doğrudan mesaj, tebrik — için yerleşik / kurum / kişisel şablonlar. Yer tutucuları SUNUCU doldurur
// (`admin_renderCoachTextTemplate`); burada yalnız liste süzgeci, gövde doğrulaması ve forma yerleştirme kuralı.
// Metinler Flutter (`coach_text_templates.dart`) ve React web (`textTemplates.ts`) ile AYNI.
//
// Bu dosya bilerek İMPORT'SUZ ve DOM'suzdur.

export const TEXT_TEMPLATES = '/v1/admin/coaching/text-templates';

export const KIND_LABELS = {
  meeting_note: 'Görüşme notu',
  question_answer: 'Soru yanıtı',
  checkin_comment: 'Check-in yorumu',
  parent_meeting: 'Veli görüşmesi',
  message: 'Mesaj',
  kudos: 'Tebrik',
};

export const SCOPE_LABELS = { built_in: 'Hazır', institution: 'Kurum', personal: 'Kişisel' };
export const SCOPE_TONES = { built_in: 'dim', institution: 'info', personal: 'good' };

/** Türün hedef alanındaki en çok uzunluk (sunucu `maxLengths` verirse o geçerli). */
export const MAX_LENGTH = {
  meeting_note: 4000, question_answer: 2000, checkin_comment: 1000, parent_meeting: 300, message: 2000, kudos: 280,
};
export const ACTION_KINDS = ['meeting_note', 'parent_meeting'];

export const PLACEHOLDER_LABELS = {
  ogrenci_adi: 'Öğrencinin adı', ogrenci_tam_adi: 'Öğrencinin adı soyadı', veli_adi: 'Velinin adı soyadı',
  koc_adi: 'Koçun / öğretmenin adı', sinif: 'Öğrencinin sınıfı', ders: 'Ders', tarih: 'Tarih', hafta: 'Hafta aralığı',
};

export const TEXTS = {
  pick: 'Şablondan doldur',
  save: 'Şablon olarak kaydet',
  manage: 'Şablonları yönet',
  search: 'Şablon ara',
  empty: 'Bu tür için şablon yok',
  emptyHint: 'Yazdığınız bir metni "Şablon olarak kaydet" ile ekleyin.',
  filled: 'Şablon forma yerleşti; dilediğiniz gibi düzenleyin.',
  saved: 'Şablon kaydedildi.',
  hint: 'Yer tutucular: {ogrenci_adi}, {ogrenci_tam_adi}, {veli_adi}, {koc_adi}, {sinif}, {ders}, {tarih}, {hafta}. '
    + 'Seçildiğinde öğrencinin bilgileriyle doldurulur.',
};

const FOLD = { İ: 'i', I: 'i', ı: 'i', Ş: 's', ş: 's', Ğ: 'g', ğ: 'g', Ü: 'u', ü: 'u', Ö: 'o', ö: 'o', Ç: 'c', ç: 'c',
  Â: 'a', â: 'a', Î: 'i', î: 'i', Û: 'u', û: 'u' };

/** Türkçe harf duyarsız arama metni (`İSTİKRAR` → `istikrar`). */
export function fold(value) {
  return String(value ?? '').replace(/[İIıŞşĞğÜüÖöÇçÂâÎîÛû]/g, (ch) => FOLD[ch]).toLowerCase()
    .split(/\s+/).filter(Boolean).join(' ');
}

/** Seçici listesi: türe göre, etkinler, arama (her sözcük başlık ya da gövdede geçmeli); sunucu sırası korunur. */
export function filterTemplates(items, { kind = '', q = '', includeInactive = false } = {}) {
  const tokens = fold(q).split(' ').filter(Boolean);
  return (items || []).filter((item) => (!kind || item.kind === kind)
    && (includeInactive || item.active !== false)
    && tokens.every((token) => fold(`${item.title} ${item.body}`).includes(token)));
}

/** İlk satırın kısa önizlemesi (liste satırı). */
export function preview(body, max = 90) {
  const line = String(body ?? '').split('\n').map((part) => part.trim()).find(Boolean) || '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** Eylem maddeleri ↔ çok satırlı metin (her satıra bir madde; boşlar atılır, en çok 10, satır ≤ 300). */
export function actionsText(items) {
  return (items || []).map((item) => (typeof item === 'string' ? item : item?.text || '')).filter(Boolean).join('\n');
}
export function parseActions(text, max = 10) {
  return String(text ?? '').split('\n').map((line) => line.replace(/^✓\s*/, '').trim()).filter(Boolean)
    .slice(0, max).map((line) => line.slice(0, 300));
}

/**
 * Doldurulmuş şablonu forma yerleştirir: alan boşsa metin olduğu gibi gelir, doluysa yazılanın ALTINA eklenir
 * (yazılan kaybolmaz). Alanın sınırı aşılırsa kesilir (`truncated`).
 */
export function mergeText(current, text, maxLength) {
  const before = String(current ?? '').trimEnd();
  const merged = before ? `${before}\n\n${text}` : String(text ?? '');
  const limit = Number(maxLength) || merged.length;
  return { text: merged.slice(0, limit), truncated: merged.length > limit };
}

/** Eylem maddelerini birleştirir (aynı metin iki kez yazılmaz). */
export function mergeActions(current, added, max = 10) {
  const out = [];
  for (const item of [...(current || []), ...(added || [])]) {
    const text = String(item ?? '').trim();
    if (text && !out.includes(text)) out.push(text);
  }
  return out.slice(0, max);
}

/** Eylem maddesi alanı olmayan formda (veli görüşmesi notu) maddeler metnin altına "Yapılacaklar" olarak eklenir. */
export function withInlineActions(text, items) {
  const list = (items || []).map((item) => String(item ?? '').trim()).filter(Boolean);
  return list.length ? `${String(text ?? '').trimEnd()}\n\nYapılacaklar:\n${list.map((item) => `- ${item}`).join('\n')}` : String(text ?? '');
}

/** Doldurulamayan yer tutucuların uyarısı (`unfilled`). */
export function unfilledNote(keys) {
  const list = (keys || []).map((key) => PLACEHOLDER_LABELS[key] || key);
  return list.length ? `Doldurulamayan yer tutucu: ${list.join(', ')} — metinde süslü parantezle kaldı.` : '';
}

/**
 * Şablon gövdesi (ekle / düzenle). `{body}` ya da `{error}`.
 * draft: {title, body, actionItems (metin ya da dizi), noteVisibility, sort, active}
 */
export function templateBody(draft, { kind, scope = null, maxLengths = MAX_LENGTH, partial = false } = {}) {
  const title = String(draft?.title ?? '').trim();
  const body = String(draft?.body ?? '').trim();
  if (!partial || draft?.title !== undefined) {
    if (title.length < 2) return { error: 'Başlık en az 2 karakter olmalı.' };
    if (title.length > 80) return { error: 'Başlık en çok 80 karakter olabilir.' };
  }
  const limit = maxLengths?.[kind] || MAX_LENGTH[kind] || 4000;
  if (!partial || draft?.body !== undefined) {
    if (!body) return { error: 'Şablon metni boş olamaz.' };
    if (body.length > limit) return { error: `${KIND_LABELS[kind] || 'Bu tür'} için metin en çok ${limit} karakter olabilir.` };
  }
  const out = {};
  if (scope) out.scope = scope;
  if (!partial) out.kind = kind;
  if (!partial || draft?.title !== undefined) out.title = title;
  if (!partial || draft?.body !== undefined) out.body = body;
  const actions = Array.isArray(draft?.actionItems) ? draft.actionItems.map((x) => String(x).trim()).filter(Boolean)
    : parseActions(draft?.actionItems);
  if (ACTION_KINDS.includes(kind)) {
    if (!partial || draft?.actionItems !== undefined) out.actionItems = actions;
  } else if (actions.length) {
    return { error: 'Eylem maddesi yalnız görüşme notu ve veli görüşmesi şablonunda olur.' };
  }
  if (kind === 'meeting_note' && (!partial || draft?.noteVisibility !== undefined)) {
    out.noteVisibility = draft?.noteVisibility || null;
  }
  if (draft?.sort !== undefined && draft.sort !== '' && draft.sort !== null) {
    const sort = Number(draft.sort);
    if (!Number.isInteger(sort) || sort < 0 || sort > 10000) return { error: 'Sıra 0–10000 arası tam sayı olmalı.' };
    out.sort = sort;
  }
  if (draft?.active !== undefined) out.active = Boolean(draft.active);
  return { body: out };
}

/** Kaydetme kapsamı: KM'de kurum şablonu (yönetiyorsa), personel hesabında kişisel. `null` = kaydedemez. */
export function saveScope(list) {
  if (list?.canCurate) return 'institution';
  if (list?.canCreatePersonal) return 'personal';
  return null;
}

/** "Bu haftayı şablon olarak kaydet": taslaktan kurum plan şablonu gövdesi (`StudyPlanTemplateInput`). */
export function weekTemplateBody(draft, name) {
  const title = String(name ?? draft?.name ?? '').trim();
  if (title.length < 2) return { error: 'Şablon adı en az 2 karakter olmalı.' };
  const tasks = (draft?.tasks || []).slice(0, 300);
  if (!tasks.length) return { error: 'Bu haftada şablona alınacak koç görevi yok.' };
  return { body: { name: title.slice(0, 80), description: draft?.description || null,
    durationDays: draft?.durationDays || 7, tasks } };
}

/** "Şablondan ekle" başlangıcı: haftanın pazartesisi geçmişteyse bugün (geçmiş güne görev yazılmaz). */
export function templateStart(weekStartIso, todayIso) {
  return String(weekStartIso) < String(todayIso) ? todayIso : weekStartIso;
}
