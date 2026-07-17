# Premium GitHub V4.1 - Dönüş Onaylı Futures

Bu sürüm V4'te gelen arka arkaya stoplardan sonra hazırlanmıştır.

## Ana değişiklik

Destek gördü diye LONG, direnç gördü diye SHORT vermez.

A kalite işlem için artık şunlar aranır:

- 4H trend aynı yönde
- 1H onay aynı yönde
- 15M dönüş mumu
- 15M EMA20 onayı
- Hacim onayı
- Stop mesafesi en az %0.80
- Risk / ödül uygunluğu
- BTC / ETH / SOL market yönü ters değil

## LONG kuralı

- Desteğe yakınlık tek başına yetmez.
- 15M yeşil kapanış gerekir.
- Fiyat EMA20 üstünde olmalıdır.
- BTC / ETH / SOL sert düşüşteyse LONG işlem sinyali verilmez.

## SHORT kuralı

- Dirence yakınlık tek başına yetmez.
- 15M kırmızı kapanış gerekir.
- Fiyat EMA20 altında olmalıdır.
- BTC / ETH / SOL sert yükselişteyse SHORT işlem sinyali verilmez.

## Sistem durur mu?

Hayır. Sistem tamamen durmaz.

Stop sayısı artarsa riskli piyasa moduna geçer:

- Maksimum işlem sinyali azalır.
- Takip radarı devam eder.
- Bot taramaya devam eder.

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

Telegram'da şu isim görünmelidir:

Premium GitHub V4.1 - Dönüş Onaylı Futures

## Uyarı

Bu bot finansal tavsiye değildir.
Kâr garantisi vermez.
Otomatik emir açmaz.
Stop mutlaka kullanılmalıdır.
