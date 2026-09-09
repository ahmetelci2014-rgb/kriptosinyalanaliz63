# Market First Profit Quality V1

## Amaç
Telegram giriş akışını tek nihai işleme düşürmek ve küçük TP geometrisini gerçek yapısal kâr potansiyeli filtresiyle değiştirmek.

## Telegram
- PREP / erken giriş: iç takip, Telegram yok.
- raw EARLY: iç takip, Telegram yok.
- Big Move: iç takip ve milestone ledger devam, ayrı Telegram yok.
- Nihai işlem: yalnız Profit Quality filtresini geçen tek giriş mesajı.
- TP/SL/BE sonuç mesajları ve günlük özet çalışmaya devam eder.

## Canlı kalite eşiği
- score >= 88
- risk <= %1.25
- 5M/uygun hacim oranı >= 0.65x
- 15M ve 1H yapı işlem yönüyle aynı
- en yakın uygun 15M / 1H / sentetik 2H yapısal hedef >= %2.00
- hedef alanı >= 2.50R
- piyasanın tercih ettiği yön açıkça ters olmamalı
- varsa target confidence ORTA veya YÜKSEK olmalı

## Yeni TP geometrisi
Seçilen yapısal hareketin:
- TP1 = %50'si
- TP2 = %75'i
- TP3 = %100'ü

Minimum %2 ana hedefte yaklaşık:
- TP1 >= %1.00
- TP2 >= %1.50
- TP3 >= %2.00

Yapısal hedef çok uzaktaysa ilk sürümde %5 hareket tavanı uygulanır.

Bu sistem kâr garantisi vermez. Ama küçük TP'leri başarı saymak yerine daha yüksek yapısal hareket alanına sahip fırsatları seçmek için tasarlanmıştır.
