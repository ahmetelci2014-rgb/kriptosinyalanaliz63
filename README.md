# Premium GitHub V4.2 - Erken TP Futures

Bu sürüm, V4/V4.1 tarafında TP1'in uzak kalması ve işlemlerin TP görmeden stopa gitmesi üzerine hazırlandı.

## Ana düzeltme

V4/V4.1'de TP1 bazı işlemlerde fazla uzak kalıyordu.
Bu sürümde TP1 daha yakın alınır.

- TP1: erken kâr hedefi
- TP2: ana hedef
- TP3: ekstra hedef

## Sistem mantığı

- Destek / direnç hesaplar.
- Trend kontrol eder.
- 1H onay arar.
- 15M dönüş mumu arar.
- Hacim kontrol eder.
- Stop mesafesine göre kaldıraç önerir.
- TP1 daha yakın hesaplanır.
- TP1 gelirse %50 kâr al, kalan işlem için SL girişe çek mantığı korunur.

## Neden bu değişti?

Önceki sürümde bazı işlemler doğru yöne kısa hareket etse bile TP1 uzak kaldığı için başarı yazamadan stopa dönebiliyordu.
V4.2 bunu azaltmak için TP1'i daha erken hedef yapar.

## Sistem durur mu?

Hayır. Sistem tamamen durmaz.
Stop sayısı artarsa riskli piyasa modunda daha seçici devam eder.

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

Premium GitHub V4.2 - Erken TP Futures

## Uyarı

Bu bot finansal tavsiye değildir.
Kâr garantisi vermez.
Otomatik emir açmaz.
Stop mutlaka kullanılmalıdır.
