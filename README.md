# Premium MTF Futures Bot v1

Bu sistem sıfırdan hazırlanmıştır.

Eski V2 / V3 / V4 / V5 dosyalarına bağlı değildir.

## Ana Mantık

- 4H ana trend belirler.
- 1H yönü onaylar.
- 15M giriş fırsatını arar.
- 5M erken radar / momentum uyarısı verir.

## Sinyal Tipleri

### A Kalite MTF Futures Sinyali

İşlem adayıdır. Açık sinyal takibine alınır.

Telegram'da gelir:

- Coin
- Yön
- Giriş
- TP1
- TP2
- TP3
- SL
- Kaldıraç önerisi
- Stop mesafesi
- 4H / 1H / 15M / 5M açıklaması
- Hacim, RSI, ADX

### Radar Uyarısı

İşlem sinyali değildir. Coin hareketleniyor diye haber verir.

## TP / SL Takibi

Bot her çalıştığında açık sinyalleri kontrol eder.

Şunları bildirir:

- TP1 geldi
- TP2 geldi
- TP3 geldi
- Stop oldu
- TP1 sonrası kalan işlem girişten kapandı
- Sinyal süresi doldu

TP1 gelince varsayılan kural:

- %50 kâr al
- Kalan işlem için SL girişe çek

## İstatistik Sistemi

performance.json içinde günlük istatistik tutulur.

Günlük raporda şunlar gelir:

- Açılan işlem sinyali
- Radar uyarısı
- LONG / SHORT sayısı
- TP1 / TP2 / TP3
- Stop sayısı
- Girişten kapanan
- Süresi dolan
- Açık sinyal
- TP1 başarı oranı
- En iyi coin
- En zayıf coin
- Son kapanan işlemler

## Risk Modu

Sistem tamamen durmaz.

Günlük stop sayısı yükselirse risk moduna geçer:

- İşlem sinyali azalır
- Radar uyarısı azalır
- Bot taramaya devam eder

## Kurulum

Zip içindeki dosyaları GitHub repo ana dizinine yükle:

- config.py
- strategy.py
- main.py
- requirements.txt
- README.md
- .github/workflows/main.yml
- open_signals.json
- performance.json
- last_signals.json

GitHub Secrets:

- TOKEN
- CHAT_ID

Telegram'da şu isim görünmelidir:

Premium MTF Futures Bot v1

## Uyarı

Bu bot finansal tavsiye değildir.
Kâr garantisi vermez.
Otomatik emir açmaz.
Futures işlemler yüksek risklidir.
Stop mutlaka kullanılmalıdır.
