// Zil sistemi — veri katmanı.
//
// TARAYICI BELLEĞİ KULLANILMAZ. 0.1 sürümünde ayar localStorage'daydı ve
// çekirdeğe taşınmıştı; artık tek doğru kaynak modülün kendi tablolarıdır.
// Bu dosya yalnız uç noktaları tek yerde toplar.
//
// GECİKMELİ YAZMA YOK. Eski panel her tuş vuruşunda kaydettiği için 600 ms
// bekletiyordu. Bu ekranda yazma anları sayılıdır (saat ekle, grup ekle,
// metni kaydet) ve her biri BİR ses üretimini tetikleyebilir — geciktirmek,
// kullanıcının ne zaman buluta çıkıldığını görememesi demek olurdu.

const BASE = '/api/bell';

let call = null;

export function connect(api) {
  call = api;
}

function need() {
  if (!call) throw new Error('Panel bağlanmadı.');
  return call;
}

/** Ayarlar, saatler, gruplar, ses durumları, ajan ve günlük — tek çağrıda. */
export function state() {
  return need()(`${BASE}/state`);
}

export function saveSettings(settings) {
  return need()(`${BASE}/settings`, { method: 'PUT', body: { settings } });
}

export function saveTimes(times) {
  return need()(`${BASE}/times`, { method: 'PUT', body: { times } });
}

/** `kind`: 'grup' (toplu ders) ya da 'ozel' (tek öğrenci). Cümle buna göre kurulur. */
export function addGroup(name, kind) {
  return need()(`${BASE}/groups`, { method: 'POST', body: { name, kind } });
}

/** `kind` boş gönderilirse mevcut tür korunur — ad değiştirmek türü sıfırlamaz. */
export function renameGroup(id, name, kind = '') {
  return need()(`${BASE}/groups/${encodeURIComponent(id)}`,
    { method: 'PUT', body: { name, kind } });
}

export function removeGroup(id) {
  return need()(`${BASE}/groups/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** Zili şimdi çalar — okulun hoparlöründen. */
export function ring() {
  return need()(`${BASE}/ring`, { method: 'POST' });
}

/** Grubu çağırır: yalnız anons, zil yok. */
export function callGroup(groupId) {
  return need()(`${BASE}/call`, { method: 'POST', body: { groupId } });
}

/**
 * Sesi YALNIZ bu bilgisayarda dinletir.
 * `ring`/`callGroup` ile karıştırılmamalı: bu okula duyulmaz.
 */
export function preview(sound, volume) {
  return need()(`${BASE}/preview`, { method: 'POST', body: { sound, volume } });
}

export function rebuildVoices() {
  return need()(`${BASE}/voices/rebuild`, { method: 'POST' });
}

export function syncAgent() {
  return need()(`${BASE}/sync`, { method: 'POST' });
}

/** Zil sesi dosyasını yükler. Tauri'de fs eklentisi yok; base64 gider. */
export async function uploadSound(file) {
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Dosya okunamadı.'));
    // `result` "data:audio/wav;base64,AAAA…" biçiminde gelir; virgülden sonrası.
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.readAsDataURL(file);
  });
  return need()(`${BASE}/sound`, { method: 'POST', body: { name: file.name, data } });
}
