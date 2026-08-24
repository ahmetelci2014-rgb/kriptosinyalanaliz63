# Kripto Sinyal Sistemi — Premium V4

Bu repo artık tek bir ana canlı sisteme odaklanır: **Premium V4**.

> Kâr garantisi yoktur. Sistem borsada otomatik emir açmaz; Telegram sinyalleri manuel değerlendirme içindir.

## Aktif mimari

### Premium V4 — ana canlı çekirdek
- OKX USDT perpetual piyasasını tarar.
- 4H ana trend + 1H teyit + 15M giriş yapısını kullanır.
- 5M erken trade canlı değildir; 5M yalnız giriş zamanlaması ve Movement Start gölge öğrenmesi için kullanılır.
- Adaylar Premium confirmation, maliyet/entry timing, cooldown/duplicate ve Portfolio Risk kontrollerinden geçer.
- Trend Continuation ve kontrollü Reversal Capture ana Premium zincirine entegredir.
- Genç/yeni coinler ayrı eski radar yerine `premium_all_coins.py` içindeki yaşa uygun kurallarla değerlendirilir.
- Telegram'a yalnız canlı Premium kapıları gerçekten geçen girişler gönderilir.

### Movement Start öğrenme katmanı
Premium ana koşusu içinde çalışır; ayrı Action harcamaz.
- V1: hareket başlangıç profili
- V2: 5M mikro yapı / squeeze / sweep / internal break
- V3: yalnız güçlü V2 adaylarında OKX order-flow

Bu katmanlar gölge/öğrenme modundadır; tek başına canlı sinyal veya emir üretmez.

### Coin Detay Analizi
`Coin Detay Analizi` workflow'u yalnız manuel çalışır.
- Premium çekirdeğin aynı karar bileşenlerini kullanır.
- Modern Telegram mikroskop raporu üretir.
- Entry / TP / SL yalnız gerçek Premium kapıları geçildiğinde gösterilir.

### Market Outlook
Market Outlook teşhis aracıdır ve yalnız manuel çalışır.
- Genel piyasa yönü, breadth ve bağlam üretir.
- Kendi başına Premium hard-gate değildir.

## GitHub Actions
Otomatik çalışan tek ana workflow:
- `main.yml` — Premium V4, 5 dakikada bir.

Manuel / gerektiğinde çalışanlar:
- `coin-analysis.yml` — Coin Detay Analizi
- `market-outlook.yml` — Market Outlook
- `tests.yml` — manuel veya pull request regresyon testleri

Eski bağımsız Scalp, Pump/Dump, Big Move, Decision/Prescription, Range, Swing, Momentum, Position Trend ve ayrı portfolio-outcome workflow'ları kaldırılmıştır. Bunlar artık Action kotası tüketmez.

## Maliyet sonrası canlı kapı
`profitability_engine.py` Premium geçmişini gerçekçi execution-cost rezerviyle değerlendirir.
Varsayılan eşikler:
- minimum örnek: 20
- minimum ortalama Net R: +0.03R
- minimum maliyet-sonrası profit factor: 1.10
- maksimum stop oranı: %32
- TP1→BE senaryosunda minimum maliyet-sonrası +0.05R

Canlı PremiumGate için ana kanıt kaynağı `trade_ledger.json` içindeki Premium 15M_ENTRY geçmişidir.

## Korunan ana state / ledger dosyaları
- `open_signals.json`
- `performance.json`
- `last_signals.json`
- `trade_ledger.json`
- `premium_pending_candidates.json`
- `premium_all_coins_state.json`
- `smart_recovery_state.json`
- `movement_start_shadow.json`
- `movement_start_v2_shadow.json`
- `movement_start_v3_orderflow_shadow.json`
- `profit_mode_report.json`
- `profit_mode_rejections.json`
- `portfolio_risk_shadow_main_mtf.json`
- `market_outlook_state.json`

## Temel ilke
**Kalite > miktar. Önce maliyet sonrası edge, sonra canlı sinyal; edge veya giriş teyidi yoksa işlem yok.**
