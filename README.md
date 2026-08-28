# Kripto Sinyal Sistemi — Market First V5

Bu repo artık ana canlı karar mantığını **Market First V5** üzerine kurar.

> Kâr garantisi yoktur. Sistem borsada otomatik emir açmaz; Telegram bildirimleri manuel değerlendirme içindir.

## Ana fikir

Eski sırada coin önce analiz ediliyor, piyasa yönü sonradan filtre gibi kullanılıyordu.
Market First V5 bunu tersine çevirir:

1. **Önce piyasa:** BTC + ETH + SOL 4H / 1H / 15M / 5M yönü okunur.
2. **Breadth:** likit OKX USDT perpetual evreninde kısa dönem ve 24 saatlik yayılım ölçülür.
3. **Piyasa rüzgârı:** `YUKARI`, `AŞAĞI` veya `KARIŞIK` belirlenir.
4. **Sonra coin:** tüm aktif OKX USDT perpetual kontratları topluca görülür; taze hareket edenler + hacimliler + rotasyon adayları derin taramaya alınır.
5. **Göreli güç:** altcoin hareketinin BTC/ETH/SOL hareketinden ne kadar ayrıştığı ölçülür.
6. **Geç kalma koruması:** 5M ATR uzaması ve 1M/3M/5M hareket genişliği yüksekse yeni giriş kovalanmaz.
7. **İşlem:** yalnız piyasa + coin + risk geometrisi birlikte uygunsa işlem fırsatı oluşur.

## Piyasa rejimleri

- `SHOCK_UP` — majörlerde ani güçlü yukarı hareket
- `BULL_STRONG` — majörler ve piyasa geneli güçlü yukarı
- `BULL` — yukarı eğilim
- `CHOP` — karışık / yönsüz
- `BEAR` — aşağı eğilim
- `BEAR_STRONG` — majörler ve piyasa geneli güçlü aşağı
- `SHOCK_DOWN` — majörlerde ani güçlü aşağı hareket

Güçlü rejimde normal karşı-yön işlemleri engellenir. Yalnız gerçekten bağımsız kırılım yapan altcoin, ayrı güçlü-hareket istisnasından değerlendirilebilir.

## Erken hareket yaşam döngüsü

Kullanıcıya karmaşık ara durumlar yerine basit takip verilir:

- `ERKEN` — hareket yeni yakalandı
- `DEVAM EDİYOR` — ilk uyarıdan sonra hareket avantajlı yönde ilerledi
- `GEÇ KALINDI` — hareket fazla uzadı; yeni giriş kovalanmaz
- `BİTTİ` — ivme başarısız oldu, güçlü geri verdi veya süre doldu

Bu takip `market_first_state.json` içinde saklanır.

## İşlem mesajı

Gerçek işlem fırsatında mesaj sade tutulur:

- yön
- piyasa yönü
- giriş
- SL
- TP1 / TP2 / TP3

Skor ve çok sayıda teknik ayrıntı kullanıcı mesajına doldurulmaz; teşhis için `market_first_diagnostics.json` içinde tutulur.

## Tarama evreni

- Borsa: **OKX USDT perpetual / swap**
- Tüm aktif uygun kontratlar her koşuda topluca görülür.
- Derin tarama kapasitesi taze fiyat hareketi, 24s hareket, hacim ve rotasyon arasında bölüştürülür.
- Böylece yalnız sabit Top-N listesine bağımlı kalınmaz.

## Risk

- Otomatik emir yok.
- Stop mesafesi için alt/üst risk sınırı vardır.
- Yakın karşı seviye hedef alanını bozuyorsa işlem üretilmez.
- Aynı coin açık işlem, stop cooldown, duplicate ve portföy çakışma korumaları mevcut operasyon katmanından korunur.
- Açık işlemlerin TP/SL/BE ve ledger takibi mevcut güvenilir takip altyapısıyla devam eder.

## Artıları

- Majör piyasa hareketini kararın merkezine alır.
- BTC/ETH/SOL sert düşerken sıradan altcoin LONG kovalamayı azaltır.
- Altcoinlerin majörlere göre göreli gücünü ölçer.
- Hareketin erken / devam / geç / bitmiş durumunu takip eder.
- Geç kalmış dik mumların arkasından giriş üretmemeye çalışır.
- Sabit 262/300 coin sınırı mantığına bağlı değildir; aktif OKX perpetual evrenini görür.
- Kullanıcı mesajlarını sade tutar; ayrıntıyı teşhis dosyasına taşır.

## Eksileri / bilinçli bedeller

- Majörlere fazla bağlı kalmak bazı bağımsız altcoin hareketlerini kaçırabilir; bu yüzden bağımsız kırılım istisnası vardır.
- Piyasa rejimi hızlı yön değiştirirse kısa süreli yanlış sınıflandırma olabilir.
- Her kontratı her koşuda 4 zaman diliminde derin taramak API açısından pahalıdır; bu yüzden ön sıralama + rotasyon kullanılır.
- Yeni listelenmiş ve yeterli mum geçmişi olmayan coinler erken radar açısından görülebilse de güvenilir SL/TP üretmek için yeterli yapı verisi olmayabilir.
- Hiçbir filtre gelecekteki fiyatı garanti edemez; sistem olasılığı ve zamanlamayı iyileştirmeyi amaçlar.

## GitHub Actions

**Otomatik çalışan tek canlı workflow:**

- `.github/workflows/main.yml` — **Market First V5**, 5 dakikada bir.

Repo içindeki eski Premium, Market Outlook, Market Structure Shadow, Big Move Research, All Contracts Momentum Radar ve benzeri yardımcı/araştırma bileşenleri **ayrı canlı sistem değildir**. Otomatik zamanlamaları kapalıdır; yalnız manuel teşhis, araştırma veya geriye dönük test amacıyla tutulur. Canlı Telegram sinyali ve ana karar akışı tek sistem olan Market First V5 üzerinden yürür.

## Ana dosyalar

- `market_first_strategy.py` — piyasa rejimi, coin skoru, geç kalma ve yaşam döngüsü
- `market_first_runner.py` — OKX evreni, breadth, rotasyon, Telegram ve canlı operasyon bağlantısı
- `test_market_first_strategy.py` — regresyon testleri
- `market_first_state.json` — çalışma sırasında oluşan durum
- `market_first_diagnostics.json` — detaylı teşhis

## Temel ilke

**Önce piyasanın rüzgârını bul; sonra o rüzgârla giden veya gerçekten bağımsız güç gösteren coini erken yakala.**
