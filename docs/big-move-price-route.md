# Büyük Hareket / Fiyat Rotası V1

## Amaç

Kısa ve sık işlemler yerine, daha az sayıda fakat daha yüksek R ve daha uzun taşıma potansiyeline sahip trend işlemlerini bulmak.

## Evren

- OKX'teki bütün aktif USDT perpetual swap piyasaları her tur keşfedilir.
- Bütün coinler ticker/likidite seviyesinde görülür.
- 24 saatlik düzeltilmiş yaklaşık notional değeri 2.000.000 USDT altındaki coinler güvenlik nedeniyle Telegram sinyaline uygun sayılmaz.

## Sinyal yolu

1. 1D ana trend yeterli olmalı.
2. 4H, 1D yönüyle uyumlu ve trend gücü yeterli olmalı.
3. BTC/ETH/SOL piyasa rejimi adayın tersinde olmamalı.
4. 1H pullback/reclaim veya breakout/retest kurulumu oluşmalı.
5. 15M kapanmış mum giriş yönünü teyit etmeli.
6. Funding aşırı ters olmamalı.
7. Yapısal stop riski yaklaşık %1-%3 aralığında olmalı.
8. Güncel fiyat 15M teyit fiyatından %0,30'dan fazla kaçmış olmamalı.
9. Skor en az 92 olmalı.
10. Ana fiyat rotası en az yaklaşık 3R ve en az %2 hareket potansiyeli taşımalı.

## Telegram

Telegram yalnız giriş onaylandığında mesaj gönderir. Mesajda:

- Coin ve yön
- Giriş bölgesi ve referans giriş
- Yapısal stop
- 1. rota
- Ana hedef bölgesi
- Uzatılmış hedef
- Tahmini ana hareket yüzdesi
- Rota dizilimi
- Setup, skor, ADX ve hacim bilgileri

Sistem daha sonra TP1, ana hedef, uzatılmış hedef, stop veya 4H trend bozulması olaylarını da Telegram'a iletir.

## Risk ve çalışma şekli

- Gerçek emir açmaz.
- Otomatik trade yapmaz.
- Telegram işlem sinyalidir; kullanıcı işlemi kendisi değerlendirir/açar.
- Kendi `big_move_route_state.json` ve `big_move_route_ledger.json` dosyalarında sanal performans takibi yapar.
- Premium, Scalp ve Pump canlı kurallarını değiştirmez.
- Aynı coin + aynı yönde 72 saatlik tekrar koruması vardır.
- Aynı anda en fazla 6 rota takip edilir ve bir turda en fazla 2 yeni Telegram sinyali açılır.
- Saatte bir çalışır; büyük hareket sistemi için 5 dakikalık tarama kullanılmaz.
