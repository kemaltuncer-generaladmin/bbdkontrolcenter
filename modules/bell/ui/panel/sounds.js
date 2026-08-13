// Zil sesleri — Web Audio ile SENTEZLENİR, dosya indirilmez.
//
// NEDEN DOSYA DEĞİL: hazır zil kayıtları telifli olur, depoya ikili dosya
// koymak K11'e aykırıdır ve masaüstü uygulaması çevrimdışı da çalışmak
// zorundadır. Buradaki tanımlar sesin TARİFİDİR; aynı tarif backend gelince
// bir kereye mahsus WAV'a dökülüp `audio` yeteneğiyle hoparlöre verilecek.
//
// Ses tasarımı: gerçek çanlar armonik değildir — kısmi sesleri (partial)
// tam kat sayılarda durmaz. Aşağıdaki oranlar bu yüzden 1.19, 2.76 gibi
// küsuratlıdır; tam katlarla yapılsaydı org sesi çıkardı, çan değil.

/** [oran, başlangıç şiddeti, sönüm süresi (sn)] */
const VOICES = {
  // Tüp çan / okul çanı — parlak, orta uzunlukta.
  chime: [[1, 1, 2.6], [2.76, 0.5, 1.9], [5.4, 0.22, 1.1], [8.9, 0.1, 0.6]],
  // Kilise çanı — hum notası ve minör üçlüsüyle daha "ağır".
  bell: [[0.5, 0.3, 4.2], [1, 1, 3.2], [1.19, 0.5, 2.4], [1.5, 0.42, 2.0],
    [2, 0.3, 1.6], [2.5, 0.18, 1.1], [3.4, 0.12, 0.7]],
  // Gong — yoğun ve uzun.
  gong: [[1, 1, 6.0], [1.36, 0.62, 5.0], [1.72, 0.48, 4.0], [2.11, 0.36, 3.2],
    [2.8, 0.26, 2.4], [3.6, 0.18, 1.6], [4.9, 0.12, 1.0]],
  // Marimba — çok kısa, yumuşak.
  marimba: [[1, 1, 1.0], [3.98, 0.26, 0.45], [9.2, 0.1, 0.25]],
};

// Notalar (Hz) — okunur kalsın diye adlandırıldı.
const G4 = 392.0;
const C5 = 523.25;
const D5 = 587.33;
const E5 = 659.25;
const A5 = 880.0;
const C6 = 1046.5;
const E6 = 1318.5;

/**
 * Zil kataloğu. Sıralama bilinçli: en tanıdık olan en üstte.
 * `preview` = dinleme düğmesinin çalacağı süre (sn).
 */
export const BELLS = [
  {
    id: 'classic_electric',
    name: 'Klasik Elektrikli Zil',
    note: 'Okulların alıştığı keskin zil. Gürültülü koridorda duyulur.',
    kind: 'buzzer',
    freq: 720,
    tremolo: 33,
    duration: 3.0,
  },
  {
    id: 'single_chime',
    name: 'Tek Çan',
    note: 'Tek vuruş, uzun sönüm. Sınav ve sessiz saatler için.',
    kind: 'strike',
    voice: 'chime',
    notes: [{ freq: D5, at: 0 }],
    duration: 3.2,
  },
  {
    id: 'ding_dong',
    name: 'İki Nota (Ding-Dong)',
    note: 'İnen iki nota. Anons öncesi dikkat çekmek için.',
    kind: 'strike',
    voice: 'chime',
    notes: [{ freq: E5, at: 0 }, { freq: C5, at: 0.55 }],
    duration: 3.4,
  },
  {
    id: 'westminster',
    name: 'Dört Nota (Westminster)',
    note: 'Klasik dört notalı çan dizisi. Tören ve giriş saatleri.',
    kind: 'strike',
    voice: 'bell',
    notes: [{ freq: E5, at: 0 }, { freq: C5, at: 0.5 }, { freq: D5, at: 1.0 }, { freq: G4, at: 1.55 }],
    duration: 5.0,
  },
  {
    id: 'gong',
    name: 'Gong',
    note: 'Kalın ve uzun. Gün başı/sonu gibi tek seferlik anlar.',
    kind: 'strike',
    voice: 'gong',
    notes: [{ freq: 116, at: 0 }],
    duration: 6.0,
  },
  {
    id: 'soft_marimba',
    name: 'Yumuşak Marimba',
    note: 'Çıkan üç nota, iki kez. Küçük yaş grupları ve etüt için.',
    kind: 'strike',
    voice: 'marimba',
    notes: [
      { freq: A5, at: 0 }, { freq: C6, at: 0.15 }, { freq: E6, at: 0.3 },
      { freq: A5, at: 0.75 }, { freq: C6, at: 0.9 }, { freq: E6, at: 1.05 },
    ],
    duration: 2.4,
  },
];

export function bellById(id) {
  return BELLS.find((bell) => bell.id === id) || BELLS[0];
}

// ------------------------------------------------------------------ çalma

let audio = null;
let master = null;
let playing = [];

function context() {
  if (!audio) {
    audio = new (window.AudioContext || window.webkitAudioContext)();
    // Sıkıştırıcı: yüksek ses düzeyinde kırpma (distortion) olmasın.
    const limiter = audio.createDynamicsCompressor();
    limiter.threshold.value = -6;
    limiter.ratio.value = 12;
    limiter.attack.value = 0.003;
    limiter.release.value = 0.25;
    master = audio.createGain();
    master.connect(limiter);
    limiter.connect(audio.destination);
  }
  if (audio.state === 'suspended') audio.resume();
  return audio;
}

/**
 * 0–100 arası kullanıcı düzeyini kazanca çevirir.
 * Doğrusal değil: kulak logaritmik duyar, doğrusal kaydırıcıda ortada
 * "hiç açılmamış" hissi olur.
 */
export function gainFor(volume) {
  const level = Math.min(100, Math.max(0, Number(volume) || 0)) / 100;
  return level ** 1.6;
}

function strike(ctx, dest, freq, at, voice) {
  for (const [ratio, level, decay] of VOICES[voice]) {
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = freq * ratio;

    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(level * 0.28, at + 0.004); // sert vuruş
    gain.gain.exponentialRampToValueAtTime(0.0001, at + decay);

    osc.connect(gain);
    gain.connect(dest);
    osc.start(at);
    osc.stop(at + decay + 0.05);
    playing.push(osc);
  }
}

function buzzer(ctx, dest, bell, at) {
  const osc = ctx.createOscillator();
  osc.type = 'square';
  osc.frequency.value = bell.freq;

  // Elektrikli zilin karakteri, çekicin hızlı vuruşundan gelen titreşimdir.
  const tremolo = ctx.createOscillator();
  tremolo.type = 'square';
  tremolo.frequency.value = bell.tremolo;
  const tremoloDepth = ctx.createGain();
  tremoloDepth.gain.value = 0.5;

  const shape = ctx.createGain();
  shape.gain.value = 0.5;
  tremolo.connect(tremoloDepth);
  tremoloDepth.connect(shape.gain);

  const tone = ctx.createBiquadFilter();
  tone.type = 'bandpass';
  tone.frequency.value = bell.freq * 1.8;
  tone.Q.value = 1.2;

  const envelope = ctx.createGain();
  envelope.gain.setValueAtTime(0, at);
  envelope.gain.linearRampToValueAtTime(0.5, at + 0.01);
  envelope.gain.setValueAtTime(0.5, at + bell.duration - 0.12);
  envelope.gain.exponentialRampToValueAtTime(0.0001, at + bell.duration);

  osc.connect(shape);
  shape.connect(tone);
  tone.connect(envelope);
  envelope.connect(dest);

  osc.start(at);
  tremolo.start(at);
  osc.stop(at + bell.duration + 0.05);
  tremolo.stop(at + bell.duration + 0.05);
  playing.push(osc, tremolo);
}

/** Zili çalar. Çalan varsa önce durdurur — üst üste binmez. */
export function play(bellId, volume = 85) {
  stop();
  const ctx = context();
  const bell = bellById(bellId);
  master.gain.value = gainFor(volume);

  const at = ctx.currentTime + 0.03;
  if (bell.kind === 'buzzer') {
    buzzer(ctx, master, bell, at);
  } else {
    for (const note of bell.notes) strike(ctx, master, note.freq, at + note.at, bell.voice);
  }
  return bell.duration;
}

export function stop() {
  for (const node of playing) {
    try {
      node.stop();
    } catch {
      // zaten durmuş
    }
  }
  playing = [];
}

/** Panel kapanırken ses donanımını bırak. */
export function release() {
  stop();
  if (audio) {
    audio.close();
    audio = null;
    master = null;
  }
}
