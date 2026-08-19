# Kripto Sinyal Sistemi — Kârlılık Modu V1

Bu repo artık **çok sinyal üretmek** yerine **az, ölçülebilir ve sermayeyi koruyan canlı işlem fikirleri** üretmek için sadeleştirilmiştir.

> Bu yazılım kâr garantisi vermez ve gerçek borsa emri açmaz. Canlı Telegram girişleri manuel karar içindir.

## Canlı çekirdek

### 1. Premium Profit Mode V1
- Kaynak: `main.py` + `strategy.py`
- Canlı giriş: ana **15M MTF** yolu.
- 5M erken trade: **canlıda kapalı**.
- Tur başına en fazla 1 yeni sinyal.
- En fazla 2 açık Premium sinyal.
- 2 stop sonrası risk modu.
- Eski ters-yön fikir yeni yönün analizini körleştirmez; aynı-yön duplicate ve portföy limitleri korunur.
- Workflow: `.github/workflows/main.yml`
- Entry point: `premium_profit_runner.py`

### 2. Scalp Profit Mode V1
- Canlı giriş: yalnız **TEPKI_SCALP**.
- PREWATCH/EARLY: Telegram'a gönderilmez.
- ATAK_SCALP: canlıda kapalı.
- TEPKI için gerçek 1M yön dönüşü zorunlu.
- Güçlü ters canlı piyasa impulsu varsa TEPKI engellenir.
- En fazla 1 açık Scalp sinyal.
- Workflow: `.github/workflows/scalp-radar.yml`
- Entry point: `scalp_profit_runner.py`

### 3. Pump/Dump Profit Mode V1
- Canlı giriş: yalnız klasik Pump/Dump kalite yolu.
- `TREND_CONTINUATION`: canlıda kapalı.
- Tüm piyasa 5/15/30M impulsu yalnız sessiz coin önceliklendirmesi için kullanılır.
- Ham impuls/erken uyarı Telegram mesajı yoktur.
- En fazla 1 açık Pump/Dump sinyal.
- Workflow: `.github/workflows/pump-radar.yml`
- Entry point: `pump_profit_runner.py`

## Araştırma / shadow

### Big Move
Büyük Hareket motoru henüz yeterli kapanmış rota örneğine sahip olmadığı için canlı Telegram'dan çıkarılmıştır.

- Saatlik taranır.
- Yalnız sanal rota ve sonuç toplar.
- Telegram sinyali göndermez.
- Gerçek emir açmaz.
- Workflow: `.github/workflows/big-move-route.yml`
- Entry point: `big_move_shadow_runner.py`

## Sessize alınan sürekli araştırma işler

Aşağıdaki deneysel yollar artık cron ile sürekli çalışmaz:
- All Market Shadow
- Momentum Shadow
- Range Shadow
- Swing Shadow
- Position Trend Shadow
- New Listing Radar

Kod/ledger geçmişi, performans incelemesi için gerektiği ölçüde korunabilir; fakat canlı karar ve Telegram yolu değildir.

## Canlı Telegram ilkesi

Telegram'a normal koşulda yalnız:
1. Gerçek işlem girişi,
2. TP / BE / SL sonucu,
3. gerekli risk disiplini

gider.

PREWATCH, EARLY, ham piyasa impulsu, yeni ve kanıtlanmamış setup'lar canlı işlem mesajı değildir.

## Robotlaştırma şartı

Otomatik emir aşamasına geçmeden önce sistemin aşağıdakileri gerçekçi işlem maliyetleriyle kanıtlaması gerekir:

- yeterli kapanmış işlem örneği,
- komisyon ve kayma sonrası pozitif beklenen değer,
- kabul edilebilir maksimum drawdown,
- setup bazında tutarlı performans,
- aynı anda açık risk için kesin sermaye limiti,
- stop ve günlük zarar kesici,
- borsa API hata/tekrar/pozisyon senkronizasyon korumaları.

Bu şartlar oluşmadan otomatik emir açma eklenmez.

## Ana veri ve denetim dosyaları

- `trade_ledger.json` — Premium işlem geçmişi
- `performance.json` — Premium performans takibi
- `scalp_performance_ledger.json` — Scalp performans geçmişi
- `pump_performance_ledger.json` — Pump/Dump performans geçmişi
- `market_impulse_guard.py` — tüm piyasa canlı impuls önceliklendirme/koruma katmanı
- `portfolio_risk.py` — ortak risk/çakışma koruması
- `decision_engine.py` — performans karar raporları
- `system_control_center.py` — teknik sistem sağlığı

## Temel ilke

**Kalite > miktar. Sermaye korunmadan getiri kovalanmaz. Kanıtı olmayan setup canlıya çıkmaz.**
