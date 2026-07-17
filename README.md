# Premium GitHub V4 - Destek Direnç Futures

Bu sürüm kaldıraçlı futures mantığına göre daha düzenli tasarlandı.

## Ana mantık

- OKX uygun USDT swap/futures coinlerini geniş tarar.
- Trend kontrol eder.
- Destek / direnç bölgesi hesaplar.
- Hacim oranını kontrol eder.
- Risk / ödül hesabı yapar.
- Stop mesafesine göre kaldıraç önerir.
- A kalite işlem sinyali ve takip radarı ayrıdır.
- Sistem stop sayısı artsa bile tamamen durmaz; riskli modda daha seçici çalışır.

## Sinyal tipleri

### A kalite futures girişi
İşlem adayıdır. TP/SL takibine alınır.

### Takip radarı
İşlem sinyali değildir. Sadece coin hareketleniyor diye uyarır.

## Kurulum

Zip içindeki dosyaları GitHub repo ana dizinine yükle.

Gerekli dosyalar:

- config.py
- strategy.py
- main.py
- requirements.txt
- README.md
- .github/workflows/main.yml
- open_signals.json
- performance.json
- last_signals.json

Telegram'da şu isim görünmelidir:

Premium GitHub V4 - Destek Direnç Futures

## Uyarı

Bu bot finansal tavsiye değildir.
Kâr garantisi vermez.
Otomatik emir açmaz.
Stop mutlaka kullanılmalıdır.
