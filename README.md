# Premium GitHub V2

Bu paket sıfırdan hazırlanmış temiz GitHub Actions kripto Telegram sinyal botudur.

## Ne yapar?

- OKX USDT swap/futures verisi kullanır.
- Hacimli ilk 120 coini tarar.
- 5 dakikada bir GitHub Actions ile çalışır.
- 5M radar ile ani hareketleri yakalamaya çalışır.
- 15M giriş, 1H onay, 4H ana yön filtresi kullanır.
- Geç giriş ve TP1'e yaklaşmış sinyalleri göndermez.
- LONG ve SHORT sinyal verebilir.
- Açık sinyalleri takip eder.
- TP1, TP2, TP3, SL ve günlük rapor gönderir.
- Otomatik emir açmaz.

## Kurulum

1. Zip içindeki dosyaları GitHub repo ana dizinine yükle.
2. `.github/workflows/main.yml` dosyasını aynı klasör yoluyla yükle.
3. GitHub Secrets içinde şunlar olmalı:
   - TOKEN
   - CHAT_ID
4. Actions > Premium GitHub V2 > Run workflow çalıştır.

## Önemli

Bu bot finansal tavsiye değildir.
Kâr garantisi vermez.
Futures işlemler çok risklidir.
İşleme girerken stop mutlaka girilmelidir.
2x - 3x üstü kaldıraç önerilmez.


## Stop Filtresi Güncellemesi

Bu sürüm BILL, ZORA, ATH ve ROBO stoplarından sonra hazırlanmıştır.

Eklenen korumalar:

- RADAR LONG sinyalinde 15M RSI 70 üstündeyse sinyal gönderilmez.
  - Amaç: ZORA gibi tepeden LONG yakalanmasını azaltmak.

- RADAR SHORT sinyalinde 15M RSI 35 altındaysa sinyal gönderilmez.
  - Amaç: BILL gibi dipten SHORT yakalanmasını azaltmak.

- Bir coin aynı gün stop olduysa o coin gün bitene kadar tekrar sinyal göndermez.
  - Amaç: ATH gibi aynı gün iki yönde de stop olan kararsız coinleri engellemek.

Normal premium sinyal sistemi korunmuştur.


## Market Koruma Güncellemesi

Bu sürüm NEARUSDT ve LTCUSDT gibi aynı anda gelen LONG stoplarından sonra hazırlanmıştır.

Eklenen korumalar:

- BTCUSDT, ETHUSDT ve SOLUSDT referans piyasa olarak kontrol edilir.
- LONG sinyali için referans coinlerin çoğunluğu 15M EMA20 üstünde olmalı.
- Piyasa 5M'de sert kırmızıysa yeni LONG sinyali gönderilmez.
- SHORT sinyali için referans coinlerin çoğunluğu 15M EMA20 altında olmalı.
- Piyasa 5M'de sert yeşilse yeni SHORT sinyali gönderilmez.
- Aynı gün aynı yönde 2 stop gelirse o yönde yeni sinyal gönderilmez.
- Maksimum gönderilen sinyal 5'ten 3'e indirildi.
- Maksimum açık sinyal 6'dan 3'e indirildi.
- Günlük stop limiti 4'ten 2'ye indirildi.

Amaç:
Piyasa topluca aşağı dönerken LONG sinyallerinin çoğalmasını azaltmak.
