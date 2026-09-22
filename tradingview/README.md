# Prime MTF Compass v1 — TradingView

Bu indikatör, GitHub'daki Premium MTF botunun grafik üstü karar-destek sürümüdür.

## Ne gösterir?

- 4H ana trend
- 1H yön onayı
- 15M trend
- 5M mikro trend
- 15M piyasa yapısı: TREND UP / TREND DOWN / RANGE / GEÇİŞ
- 1M RSI
- 15M hacim oranı ve ADX uyumu
- Giriş bölgesi uzaklığı
- Breakout / fake-break / retest
- Supertrend uyumu
- Ichimoku uyumu
- Fibonacci geri çekilme bölgesi
- Shadow giriş-kalitesi skoru
- BOT-UYUMLU LONG / SHORT veya GÖZLEM durumu

## TradingView'e kurulum

1. TradingView'de herhangi bir grafik aç.
2. Alt bölümden **Pine Editor** ekranını aç.
3. `prime_mtf_compass_v1.pine` dosyasının tamamını kopyala.
4. Pine Editor'e yapıştır.
5. **Save** ve ardından **Add to chart** seç.
6. İstersen indikatör ayarlarından zaman dilimlerini ve görünümü değiştir.

## Alarm

TradingView alarm ekranında indikatörü seçtiğinde şu koşullar kullanılabilir:

- Prime MTF - BOT UYUMLU LONG
- Prime MTF - BOT UYUMLU SHORT
- Prime MTF - GÖZLEM LONG
- Prime MTF - GÖZLEM SHORT

## Bot ile farkı

Bu gösterge yalnız açık olan grafiği değerlendirir. GitHub botu ise çok sayıda OKX USDT futures paritesini tarar ve ayrıca Market Guard, Portfolio Risk, açık işlem limiti, duplicate/cooldown ve ledger takibi uygular.

Bu nedenle TradingView'deki **BOT-UYUMLU ADAY**, botun Telegram'a kesin işlem göndereceği anlamına gelmez. Ama aynı MTF yaklaşımıyla grafikte hızlı kontrol sağlar.

## Varsayılan mantık

- 4H: ana yön
- 1H: kesin onay
- 15M: EMA pullback/reclaim + MACD + RSI + ADX + hacim
- 5M: mikro yön
- Shadow: Market Structure + Supertrend + Ichimoku + Breakout/Retest + Fibonacci

Shadow bileşenleri ilk aşamada botta hard-block değildir. Gerçek işlem sonuçları ledger'da biriktikçe hangilerinin filtreye dönüşeceği ayrıca ölçülür.
