# Day Trading Core V1

Bu sürüm Market First canlı giriş zincirini tamamen devreden çıkarıp tek bir ölçülebilir intraday çekirdeğe geçer. Eski dosyalar yalnız geri dönüş/tarihçe için repoda kalır; canlı karar vermez.

## Canlı karar sırası

1. **2H yön:** EMA20/EMA50 trendi + RSI + aşırı uzama kontrolü.
2. **15M setup:** trend yönünde EMA20 çevresine pullback ve tekrar trend tarafında kapanış.
3. **5M tetik:** EMA9/EMA20 momentum, önceki iki mum kırılımı, hacim ve mum gövdesi teyidi.
4. **Yapısal stop:** son 15M swing + ATR tamponu. Stop mesafesi %0.45-%1.25 dışında ise işlem yok.
5. **Geç giriş koruması:** canlı fiyat, teyit mumundan 0.35R'den fazla uzaklaşmışsa işlem yok.

## Kâr yönetimi

- TP1 = 1.2R, pozisyonun %35'i.
- TP2 = 2.0R, pozisyonun %35'i.
- TP3 = 3.0R, pozisyonun %30'u.
- TP1 sonrası koruma stopu +0.1R.
- TP2 sonrası koruma stopu +1.0R.
- Tam TP3 yolu brüt yaklaşık +2.02R üretir.
- TP1 sonrası koruma stopu brüt yaklaşık +0.485R bırakır.
- Her kapanan işlem için performans hesabında tahmini 0.15R işlem maliyeti düşülür.

## Günlük risk frenleri

- Aynı anda en fazla 2 açık day trade.
- Aynı yönde en fazla 1 açık day trade.
- Günde en fazla 4 yeni sinyal.
- Günde 2 tam stop sonrası yeni işlem yok.
- Günlük net model sonucu -1.5R veya altına düşerse yeni işlem yok.
- Bir işlem en fazla 6 saat takip edilir; sonra kalan pozisyon modelde zaman çıkışıyla kapatılır.

## Ölçüm

Yeni sistem geçmiş Market First ledger'ını kullanmaz. Aşağıdaki dosyalar sıfırdan oluşur:

- `day_trading_state.json`
- `day_trading_ledger.json`
- `day_trading_summary.json`
- `day_trading_diagnostics.json`

Ana başarı ölçüsü **net R expectancy**'dir:

`toplam net R / kapanan işlem sayısı`

TP sayısı veya skor tek başına başarı kabul edilmez.

## Emir davranışı

Sistem OKX'te otomatik emir açmaz. Telegram'a manuel işlem için giriş/stop/TP seviyeleri gönderir ve kendi model ledger'ında sonucu takip eder.
