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
