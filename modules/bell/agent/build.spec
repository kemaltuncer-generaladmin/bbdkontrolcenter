# PyInstaller yapılandırması — BBD Zil Ajanı.
#
#   pip install pyinstaller
#   pyinstaller build.spec
#   → dist/bbd-zil.exe
#
# TEK DOSYA ve PENCERESİZ. Zil bilgisayarında Python kurulu değil ve kimse
# başında durmuyor; ekranda bir konsol penceresinin açık kalması hem çirkin
# hem de yanlışlıkla kapatılmaya davetiye.
#
# HARİCİ BAĞIMLILIK YOK. `agent.py` yalnız standart kütüphane kullanıyor, bu
# yüzden `hiddenimports` boş ve dosya küçük kalıyor (~8 MB).
#
# `console=False` ile `sys.stdout` None olur; `agent.py` bunu biliyor ve
# günlüğü yalnız dosyaya yazar.

a = Analysis(
    ['agent.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Standart kütüphanenin ağır ve gereksiz parçaları dışarıda bırakılır.
    excludes=['tkinter', 'unittest', 'pydoc', 'doctest', 'test', 'email',
              'xml', 'sqlite3', 'multiprocessing', 'asyncio'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='bbd-zil',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX sıkıştırması bazı virüs tarayıcılarını tetikliyor
    console=False,      # arayüz yok — pencere de yok
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
