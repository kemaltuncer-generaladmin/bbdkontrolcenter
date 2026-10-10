// Öğrenci Tablet Yönetimi — profiller, fiziksel tabletler ve öğrenci ek süresi.
const h = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};

function field(label, input) {
  const box = h('label', 'tb-field');
  box.append(h('span', null, label), input);
  return box;
}

function input(value = '', type = 'text') {
  const node = h('input', 'tb-input');
  node.type = type;
  node.value = value;
  return node;
}

function button(label, onClick, primary = false) {
  const node = h('button', `tb-button${primary ? ' primary' : ''}`, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

function option(value, label) {
  return new Option(label, value);
}

function displayDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('tr-TR', {
    dateStyle: 'short', timeStyle: 'short',
  });
}

// Sunucunun kabul ettiği Android SystemPolicyControl.storageKey() alt kümesi.
// Boş seçim, tabletteki mevcut varsayılan davranışı korur.
const SYSTEM_CONTROLS = [
  ['usb_file_transfer', 'USB dosya aktarımı', 'Tabletin USB üzerinden depolama olarak kullanılmasını engeller.'],
  ['unknown_source_installs', 'Bilinmeyen kaynaklardan kurulum', 'Mağaza dışı uygulama kurulumunu engeller.'],
  ['app_stores_and_installers', 'Uygulama mağazaları ve yükleyiciler', 'Uygulama kurulumunu ve tablet uygulamasının güvenli güncellemesini de engelleyebilir.'],
  ['account_modification', 'Hesap değişiklikleri', 'Cihazda hesap ekleme ve kaldırmayı engeller.'],
  ['vpn_configuration', 'VPN yapılandırması', 'VPN ayarlarının değiştirilmesini engeller.'],
  ['network_reset', 'Ağ ayarlarını sıfırlama', 'Ağ ayarlarını sıfırlamayı engeller.'],
  ['tethering', 'İnternet paylaşımı', 'Hotspot ve bağlantı paylaşımı ayarlarını engeller.'],
  ['wifi_configuration', 'Wi-Fi yapılandırması', 'Ağ seçimini kısıtlar; yeni bir Wi-Fi ağı veya şifresi tanımlamaz. Cihaz üreticisine göre sonuç değişebilir.'],
  ['private_dns', 'Özel DNS', 'Özel DNS ayarlarının değiştirilmesini engeller.'],
  ['date_and_time', 'Tarih ve saat', 'Sistem saati ve saat dilimi değişikliklerini engeller.'],
  ['safe_boot', 'Güvenli mod', 'Cihazın güvenli modda başlatılmasını engeller.'],
  ['factory_reset', 'Fabrika ayarlarına sıfırlama', 'Ayarlar üzerinden cihazı sıfırlamayı engeller.'],
  ['user_and_profile_creation', 'Kullanıcı ve profil oluşturma', 'Yeni kullanıcı/profil oluşturmayı ve kullanıcı değiştirmeyi engeller.'],
  ['app_uninstall', 'Uygulama kaldırma', 'Uygulamaların kaldırılmasını engeller.'],
  ['app_control_settings', 'Uygulama kontrol ayarları', 'Uygulamaları durdurma ve verilerini silme ayarlarını engeller.'],
];

export function mount(root, ctx) {
  const styleHref = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${styleHref}"]`)) {
    const link = h('link');
    link.rel = 'stylesheet';
    link.href = styleHref;
    document.head.append(link);
  }

  const state = { profiles: [], devices: [], sessions: [], students: [],
    selectedProfile: '', selectedDevice: '', selectedStudent: '', inventory: [], deviceUsage: null };
  const view = h('div', 'tb');
  const heading = h('header', 'tb-heading');
  heading.append(h('div', null, 'Öğrenci tabletleri'));
  heading.append(button('Yenile', () => refresh()));
  const notice = h('p', 'tb-notice', 'Yükleniyor…');
  const tabs = h('nav', 'tb-tabs');
  const body = h('div', 'tb-body');
  view.append(heading, notice, tabs, body);
  root.replaceChildren(view);

  let activeTab = 'devices';
  function info(message, bad = false) {
    notice.textContent = message;
    notice.classList.toggle('bad', bad);
  }
  async function call(path, options) {
    return ctx.api(`/api/bbd_tablet${path}`, options);
  }
  async function loadSelectedDeviceData() {
    const device = state.devices.find((item) => item.id === state.selectedDevice);
    if (!device) {
      state.inventory = [];
      state.deviceUsage = null;
      return;
    }
    try { state.inventory = await call(`/devices/${encodeURIComponent(device.id)}/apps`); }
    catch { state.inventory = []; }
    const session = state.sessions.find((item) => item.device_id === device.id);
    if (!session) {
      state.deviceUsage = null;
      return;
    }
    const profile = state.profiles.find((item) => item.id === device.profile_id);
    const timezone = profile?.timezone || 'Europe/Istanbul';
    const localDate = new Date().toLocaleDateString('sv-SE', { timeZone: timezone });
    try {
      const result = await call(`/students/${encodeURIComponent(session.student_id)}/usage?localDate=${localDate}`);
      state.deviceUsage = { studentId: session.student_id, localDate, ...result };
    } catch (error) {
      state.deviceUsage = { studentId: session.student_id, localDate, error: error.message };
    }
  }
  async function refresh() {
    try {
      const overview = await call('/overview');
      let students = [];
      let canteenOnline = true;
      try { students = await call('/students'); }
      catch { canteenOnline = false; }
      state.profiles = overview.profiles || [];
      state.devices = overview.devices || [];
      state.sessions = overview.sessions || [];
      state.students = students || [];
      if (!state.selectedProfile) state.selectedProfile = state.profiles[0]?.id || '';
      if (!state.selectedDevice) state.selectedDevice = state.devices[0]?.id || '';
      if (!state.selectedStudent) state.selectedStudent = state.students[0]?.id || '';
      await loadSelectedDeviceData();
      info(`${state.devices.length} tablet · ${state.profiles.length} profil · ${state.sessions.length} açık öğrenci oturumu${canteenOnline ? '' : ' · Kantin öğrenci listesine ulaşılamadı'}`);
      render();
    } catch (error) {
      info(`Veriler alınamadı: ${error.message}`, true);
    }
  }
  function render() {
    tabs.replaceChildren();
    for (const [key, label] of [
      ['devices', 'Tabletler'], ['profiles', 'Profiller'], ['students', 'Kullanım ve ek süre'],
    ]) {
      const tab = button(label, () => { activeTab = key; render(); });
      tab.classList.toggle('active', key === activeTab);
      tabs.append(tab);
    }
    body.replaceChildren();
    if (activeTab === 'devices') renderDevices();
    if (activeTab === 'profiles') renderProfiles();
    if (activeTab === 'students') renderStudents();
  }
  function profilePicker(value) {
    const select = h('select', 'tb-input');
    for (const profile of state.profiles) select.append(option(profile.id, profile.name));
    select.value = value || '';
    return select;
  }
  function renderDevices() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Kayıtlı tabletler'));
    if (!state.devices.length) list.append(h('p', 'tb-muted', 'Henüz tablet kaydı yok.'));
    for (const device of state.devices) {
      const row = h('button', `tb-list-row${state.selectedDevice === device.id ? ' selected' : ''}`);
      row.type = 'button';
      row.append(h('strong', null, device.name),
        h('small', null, device.last_seen_at
          ? `Son bağlantı: ${displayDateTime(device.last_seen_at)}` : 'Henüz eşlenmedi'));
      row.addEventListener('click', async () => {
        state.selectedDevice = device.id;
        await loadSelectedDeviceData();
        render();
      });
      list.append(row);
    }
    const detail = h('section', 'tb-card');
    const device = state.devices.find((item) => item.id === state.selectedDevice);
    detail.append(h('h3', null, device ? device.name : 'Yeni tablet'));
    if (device) {
      const profile = state.profiles.find((item) => item.id === device.profile_id);
      const session = state.sessions.find((item) => item.device_id === device.id);
      detail.append(h('p', device.revoked_at ? 'tb-error' : 'tb-muted',
        device.revoked_at ? 'Bağlantı iptal edildi' : !device.last_seen_at
          ? 'Eşleşme bekleniyor' : device.online ? 'Çevrimiçi' : 'Çevrimdışı'));
      detail.append(h('p', 'tb-muted', device.last_seen_at
        ? `Son görülme: ${displayDateTime(device.last_seen_at)}` : 'Henüz bağlantı kurulmadı'));
      detail.append(h('p', 'tb-muted', `${device.manufacturer || ''} ${device.model || ''} · ${device.app_version || 'Sürüm bilgisi yok'}`));
      detail.append(h('p', 'tb-muted', `İlke durumu: ${device.policy_health || 'Henüz bildirilmedi'}`));
      detail.append(h('p', 'tb-muted', `Profil: ${profile ? `${profile.name} · hedef sürüm ${profile.revision}` : 'Atanmamış'}`));
      const appliedRevision = Number(device.applied_profile_revision || 0);
      const targetRevision = Number(profile?.revision || 0);
      const reportedProfileMatches = Boolean(device.profile_id &&
        device.applied_profile_id === device.profile_id);
      const appliedIsCurrent = reportedProfileMatches && appliedRevision === targetRevision;
      const appliedIsBehind = reportedProfileMatches && appliedRevision > 0 && appliedRevision < targetRevision;
      const appliedIsInconsistent = reportedProfileMatches && appliedRevision > targetRevision;
      detail.append(h('p', appliedIsCurrent ? 'tb-muted' : 'tb-error',
        !profile ? 'Tablete profil atanmamış.'
          : !reportedProfileMatches ? 'Tablet yeni atanmış profili henüz uyguladığını bildirmedi.'
          : appliedIsCurrent ? `Son canlılık bildiriminde sürüm ${appliedRevision} bildirildi · güncel`
            : appliedIsBehind ? `Son canlılık bildiriminde sürüm ${appliedRevision} bildirildi · güncel değil`
              : appliedIsInconsistent ? `Bildirilen sürüm hedefin üzerinde (${appliedRevision} / ${targetRevision}).`
                  : 'Tablet politika uygulamasını henüz bildirmedi.'));
      detail.append(h('p', 'tb-muted', session
        ? `Etkin öğrenci: ${session.student_name || session.student_id} · giriş ${displayDateTime(session.login_at)}`
        : 'Etkin öğrenci oturumu yok.'));
      const telemetry = h('section', 'tb-card');
      telemetry.append(h('h4', null, 'Gerçek kullanım telemetrisi'));
      if (!session) {
        telemetry.append(h('p', 'tb-muted', 'Telemetri, tablet üzerinde etkin öğrenci oturumu başladığında görünür.'));
      } else if (state.deviceUsage?.studentId !== session.student_id) {
        telemetry.append(h('p', 'tb-muted', 'Kullanım verisi yükleniyor…'));
      } else if (state.deviceUsage.error) {
        telemetry.append(h('p', 'tb-error', `Kullanım verisi alınamadı: ${state.deviceUsage.error}`));
      } else {
        telemetry.append(h('p', 'tb-muted', `${state.deviceUsage.localDate} · ${session.student_name || session.student_id}`));
        const entries = new Map();
        for (const row of state.deviceUsage.usage || []) {
          entries.set(row.packageName, { usedSeconds: row.usedSeconds, extraSeconds: 0 });
        }
        for (const grant of state.deviceUsage.grants || []) {
          const item = entries.get(grant.packageName) || { usedSeconds: 0, extraSeconds: 0 };
          item.extraSeconds += grant.extraSeconds;
          entries.set(grant.packageName, item);
        }
        if (!entries.size) telemetry.append(h('p', 'tb-muted', 'Bu tarih için henüz kullanım kaydı yok.'));
        for (const [packageName, item] of entries) {
          telemetry.append(h('p', 'tb-usage', `${packageName} · Kullanım ${Math.floor(item.usedSeconds / 60)} dk · Ek süre ${Math.floor(item.extraSeconds / 60)} dk`));
        }
      }
      detail.append(telemetry);
      if (device.revoked_at) {
        detail.append(h('p', 'tb-muted', 'Tablet eşlemesi kaldırıldı; aynı donanım yeni bir eşleme koduyla tekrar kaydedilebilir.'));
      } else {
        detail.append(button('Tableti eşlemeden çıkar', async () => {
          const approved = window.confirm(`${device.name} tabletinin eşlemesini kaldırmak ve açık öğrenci oturumunu kapatmak istiyor musunuz? Cihaz kimliği ve erişim anahtarı iptal edilir.`);
          if (!approved) return;
          try {
            await call(`/devices/${encodeURIComponent(device.id)}/revoke`, { method: 'POST' });
            await refresh();
            info('Tablet eşlemesi kaldırıldı. Cihazı yeniden kaydetmek için yeni eşleme kodu oluşturun.');
          } catch (error) { info(error.message, true); }
        }));
      }
      if (!device.profile_id && state.inventory.some((app) => app.isLaunchable)) {
        detail.append(h('p', 'tb-muted', 'İlk profil bu tablet eşleştikten sonra yüklü uygulamalardan oluşturulabilir.'));
        detail.append(button('Uygulamalardan profil oluştur ve ata', async () => {
          const apps = state.inventory.filter((app) => app.isLaunchable).map((app) => ({
            packageName: app.packageName, appName: app.appName || app.packageName,
            allowed: true, unlimited: false, dailyLimitSeconds: 3600,
          }));
          try {
            const profile = await call('/profiles', {
              method: 'POST',
              body: { name: `${device.name} başlangıç profili`, timezone: 'Europe/Istanbul', apps },
            });
            await call(`/devices/${encodeURIComponent(device.id)}/assign-profile`, {
              method: 'POST', body: { profileId: profile.id },
            });
            state.selectedProfile = profile.id;
            await refresh();
            info(`${profile.name} oluşturuldu ve tablete atandı. Uygulamalar varsayılan olarak izinli, günlük 60 dakika.`);
          } catch (error) { info(error.message, true); }
        }, true));
      }
      if (!device.revoked_at) {
        const picker = profilePicker(device.profile_id);
        detail.append(field('Atanmış profil', picker));
        detail.append(button('Profili ata', async () => {
          try {
            await call(`/devices/${encodeURIComponent(device.id)}/assign-profile`, {
              method: 'POST', body: { profileId: picker.value },
            });
            await refresh();
            info('Profil atandı. Tablet yeni profili bir sonraki eşitlemede alacak.');
          } catch (error) { info(error.message, true); }
        }, true));
      }
      const inventory = h('div', 'tb-inventory');
      inventory.append(h('h4', null, `Yüklü uygulamalar (${state.inventory.length})`));
      for (const app of state.inventory) inventory.append(h('p', null,
        `${app.appName || app.packageName} · ${app.packageName}`));
      detail.append(inventory);
    }
    const form = h('div', 'tb-form');
    form.append(h('h4', null, 'İlk tablet eşleştirme'));
    const name = input('Tablet', 'text');
    form.append(h('p', 'tb-muted', 'Profil seçmeden kod oluşturabilirsiniz. Tabletin yüklü uygulama listesini aldıktan sonra başlangıç profilini bu ekrandan oluşturabilirsiniz.'),
      field('Tablet adı', name),
      button('Eşleme kodu oluştur', async () => {
        try {
          const result = await call('/devices', {
            method: 'POST', body: { name: name.value.trim() || 'Tablet' },
          });
          await refresh();
          const codeOut = h('p', 'tb-code',
            `Tek kullanımlık eşleme kodu: ${result.enrollmentCode} · Geçerlilik sonu: ${displayDateTime(result.expiresAt)}`);
          body.querySelector('.tb-card:last-child')?.append(codeOut);
        } catch (error) { info(error.message, true); }
      }, true));
    detail.append(form);
    columns.append(list, detail);
    body.append(columns);
  }
  function renderProfiles() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Ortak profiller'));
    for (const profile of state.profiles) {
      const row = h('button', `tb-list-row${state.selectedProfile === profile.id ? ' selected' : ''}`);
      row.type = 'button';
      row.append(h('strong', null, profile.name), h('small', null, `Sürüm ${profile.revision} · ${profile.apps.length} uygulama`));
      row.addEventListener('click', () => { state.selectedProfile = profile.id; render(); });
      list.append(row);
    }
    list.append(button('+ Yeni profil', () => { state.selectedProfile = ''; render(); }));
    const detail = h('section', 'tb-card');
    const profile = state.profiles.find((item) => item.id === state.selectedProfile);
    detail.append(h('h3', null, profile ? `Profil · sürüm ${profile.revision}` : 'Yeni profil'));
    const name = input(profile?.name || '');
    const timezone = input(profile?.timezone || 'Europe/Istanbul');
    detail.append(field('Profil adı', name), field('Saat dilimi', timezone));
    const controls = h('section', 'tb-controls');
    controls.append(h('h4', null, 'Uzaktan sistem kontrolleri'),
      h('p', 'tb-muted', 'Açık: kısıtlamayı uygula · Kapalı: kısıtlamayı kaldır · Cihaz varsayılanı: mevcut davranışı koru. Tablet, desteklemediği kısıtlamayı uygulayamayabilir. Geliştirici seçenekleri ve USB hata ayıklama uzaktan değiştirilemez; kurtarma erişimi korunur.'));
    const controlChoices = new Map();
    for (const [key, label, description] of SYSTEM_CONTROLS) {
      const row = h('div', 'tb-control-row');
      const descriptionBox = h('div');
      descriptionBox.append(h('strong', null, label), h('small', null, description));
      const choice = h('select', 'tb-input');
      choice.append(option('', 'Cihaz varsayılanı'), option('true', 'Açık'), option('false', 'Kapalı'));
      if (Object.hasOwn(profile?.systemControls || {}, key)) {
        choice.value = profile.systemControls[key] ? 'true' : 'false';
      }
      controlChoices.set(key, choice);
      row.append(descriptionBox, choice);
      controls.append(row);
    }
    detail.append(controls);
    const appList = h('div', 'tb-apps');
    const appRows = [];
    function addApp(app = {}) {
      const row = h('div', 'tb-app-row');
      const pkg = input(app.packageName || '');
      pkg.placeholder = 'com.example.app';
      const label = input(app.appName || '');
      label.placeholder = 'Uygulama adı';
      const allowed = input('', 'checkbox');
      allowed.checked = Boolean(app.allowed);
      const unlimited = input('', 'checkbox');
      unlimited.checked = Boolean(app.unlimited);
      const minutes = input(String(Math.floor((app.dailyLimitSeconds || 0) / 60)), 'number');
      minutes.min = '0';
      minutes.max = '1440';
      row.append(field('Uygulama paketi', pkg), field('Uygulama adı', label), field('İzin ver', allowed),
        field('Sınırsız', unlimited), field('Günlük süre (dk)', minutes));
      row.append(button('Kaldır', () => { row.remove(); appRows.splice(appRows.indexOf(item), 1); }));
      const item = { row, pkg, label, allowed, unlimited, minutes };
      appRows.push(item);
      appList.append(row);
    }
    for (const app of profile?.apps || []) addApp(app);
    const inventorySelect = h('select', 'tb-input');
    for (const app of state.inventory.filter((item) => item.isLaunchable)) {
      inventorySelect.append(option(app.packageName,
        `${app.appName || app.packageName} · ${app.packageName}`));
    }
    detail.append(h('h4', null, 'Uygulama kuralları'), appList,
      field('Seçili tabletteki uygulamalar', inventorySelect),
      button('Tabletten ekle', () => {
        const app = state.inventory.find((item) => item.packageName === inventorySelect.value);
        if (app && !appRows.some((item) => item.pkg.value === app.packageName)) {
          addApp({ ...app, allowed: true, unlimited: false, dailyLimitSeconds: 3600 });
        }
      }),
      button('+ Uygulama ekle', () => addApp()),
      button('Profili kaydet', async () => {
        const apps = appRows.map((item) => ({
          packageName: item.pkg.value.trim(), appName: item.label.value.trim(),
          allowed: item.allowed.checked, unlimited: item.unlimited.checked,
          dailyLimitSeconds: Number(item.minutes.value) * 60,
        })).filter((app) => app.packageName);
        const systemControls = {};
        for (const [key, choice] of controlChoices) {
          if (choice.value) systemControls[key] = choice.value === 'true';
        }
        try {
          const result = await call(profile ? `/profiles/${encodeURIComponent(profile.id)}` : '/profiles', {
            method: profile ? 'PUT' : 'POST',
            body: { name: name.value.trim(), timezone: timezone.value.trim(), apps, systemControls },
          });
          state.selectedProfile = result.id;
          await refresh();
          info(`Profil kaydedildi · sürüm ${result.revision}`);
        } catch (error) { info(error.message, true); }
      }, true));
    columns.append(list, detail);
    body.append(columns);
  }
  async function renderStudentDetail(detail, studentId, date) {
    try {
      const result = await call(`/students/${encodeURIComponent(studentId)}/usage?localDate=${date}`);
      const student = state.students.find((item) => item.id === studentId);
      const session = state.sessions.find((item) => item.student_id === studentId);
      const device = state.devices.find((item) => item.id === session?.device_id);
      const assignedProfile = state.profiles.find((item) => item.id === device?.profile_id);
      detail.replaceChildren(h('h3', null, student?.name || 'Öğrenci'),
        h('p', 'tb-muted', `${date} · Günlük kullanım ve ek süre`));
      const map = new Map();
      for (const row of result.usage || []) map.set(row.packageName, { used: row.usedSeconds, extra: 0 });
      for (const row of result.grants || []) {
        const item = map.get(row.packageName) || { used: 0, extra: 0 };
        item.extra += row.extraSeconds;
        map.set(row.packageName, item);
      }
      if (!map.size) detail.append(h('p', 'tb-muted', 'Bugün için kullanım veya ek süre kaydı yok.'));
      for (const [pkg, value] of map) detail.append(h('p', 'tb-usage',
        `${pkg} · Kullanılan: ${Math.floor(value.used / 60)} dk · Ek süre: ${Math.floor(value.extra / 60)} dk`));
      if (!session || !assignedProfile) {
        detail.append(h('p', 'tb-muted',
          'Ek süre vermek için öğrencinin geçerli profili olan bir tablette açık oturumu bulunmalı.'));
      } else {
        const allowedApps = assignedProfile.apps.filter((app) => app.allowed);
        if (!allowedApps.length) {
          detail.append(h('p', 'tb-muted', 'Öğrencinin etkin tablet profilinde izinli uygulama yok.'));
        } else {
          const select = h('select', 'tb-input');
          for (const app of allowedApps) {
            select.append(option(app.packageName, `${app.appName || app.packageName} · ${app.packageName}`));
          }
          const minutes = input('10', 'number');
          minutes.min = '1';
          minutes.max = '1440';
          const reason = input('');
          reason.placeholder = 'Gerekçe (isteğe bağlı)';
          detail.append(field('Uygulama', select), field('Ek süre (dk)', minutes),
            field('Gerekçe', reason), button('Ek süre ver', async () => {
              try {
                await call(`/students/${encodeURIComponent(studentId)}/apps/${encodeURIComponent(select.value)}/grants`, {
                  method: 'POST', body: { localDate: date, extraSeconds: Number(minutes.value) * 60,
                    reason: reason.value.trim() },
                });
                await renderStudentDetail(detail, studentId, date);
                info('Ek süre öğrencinin etkin tablet profilindeki uygulamaya eklendi.');
              } catch (error) { info(error.message, true); }
            }, true));
        }
      }
    } catch (error) { detail.replaceChildren(h('p', 'tb-error', error.message)); }
  }
  function renderStudents() {
    const columns = h('div', 'tb-columns');
    const list = h('section', 'tb-card');
    list.append(h('h3', null, 'Öğrenciler'), h('p', 'tb-muted',
      'Öğrenciler tabletlerde, kantin kiosklarında kullandıkları şifreyle giriş yapar.'));
    const search = input('', 'search');
    search.placeholder = 'Öğrenci ara';
    const rows = h('div', 'tb-students');
    function paintStudents() {
      rows.replaceChildren();
      const q = search.value.toLocaleLowerCase('tr');
      for (const student of state.students.filter((item) => item.name.toLocaleLowerCase('tr').includes(q))) {
        const row = button(student.name || student.id, () => {
          state.selectedStudent = student.id;
          renderStudents();
        });
        row.classList.add('tb-list-row');
        row.classList.toggle('selected', student.id === state.selectedStudent);
        rows.append(row);
      }
    }
    search.addEventListener('input', paintStudents);
    list.append(search, rows);
    paintStudents();
    const detail = h('section', 'tb-card');
    const selected = state.students.find((item) => item.id === state.selectedStudent);
    const session = state.sessions.find((item) => item.student_id === selected?.id);
    const device = state.devices.find((item) => item.id === session?.device_id);
    const profile = state.profiles.find((item) => item.id === device?.profile_id)
      || state.profiles.find((item) => item.id === state.selectedProfile);
    const zone = profile?.timezone || 'Europe/Istanbul';
    const date = new Date().toLocaleDateString('sv-SE', { timeZone: zone });
    if (selected) {
      detail.append(h('h3', null, selected.name), h('p', 'tb-muted', `${date} · ${zone}`));
      renderStudentDetail(detail, selected.id, date);
    }
    columns.append(list, detail);
    body.append(columns);
  }
  refresh();
  return () => root.replaceChildren();
}
