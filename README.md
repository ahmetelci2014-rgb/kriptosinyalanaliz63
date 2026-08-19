# Kripto Sinyal Sistemi — Kârlılık Modu V2

Bu repo artık sinyal sayısını değil **maliyet sonrası beklenen değeri** merkeze alır.

> Kâr garantisi yoktur. Sistem gerçek borsa emri açmaz; Telegram girişleri manuel karar içindir.

## Profit Mode V2 karar zinciri

### Premium — ana canlı çekirdek
- Yalnız 15M MTF giriş yolu canlıdır; 5M erken trade kapalıdır.
- Yeni aday önce mevcut teknik filtreleri geçer.
- Ardından canlı fiyatın TP1 yolunda en az %5 ilerlemiş, en fazla %40 ilerlemiş olması gerekir.
- Girişten canlı fiyat uzaklığı yaklaşık %0.08–%0.25 teyit koridorunda olmalıdır.
- Stop çok dar olduğu için komisyon/kayma sonrası TP1→BE sonucu zayıf kalacaksa işlem reddedilir.
- Aynı tip geçmiş 15M işlemler maliyet sonrası yeniden hesaplanır.
- Minimum örnek, ortalama Net R, profit factor ve stop oranı şartları geçmeden Telegram'a gerçek giriş çıkmaz.
- Reddedilen adaylar `profit_mode_rejections.json` içinde sessizce tutulur.
- Kapanmış Premium işlemlerinde eski `r_result` korunur; ayrıca `gross_r_before_costs`, `estimated_execution_cost_r` ve `net_r_after_costs` alanları eklenir.

### Scalp
- ATAK_SCALP canlı değildir.
- TEPKI_SCALP gerçek 1M dönüş teyidi + ters canlı impuls korumasını korur.
- TEPKI maliyet sonrası yeterli örneğe ulaşmadıysa Telegram'a çıkmaz; sanal olarak takip edilip örnek büyütür.
- Yeterli örnek ve pozitif maliyet-sonrası performans oluşursa otomatik canlıya hak kazanır.

### Pump/Dump
- Tüm piyasa 5/15/30M impulsu yalnız sessiz önceliklendirme katmanıdır.
- Trend Continuation canlı değildir.
- Mevcut açık Pump sinyalleri sonuçlanana kadar takip edilir.
- Yeni Pump girişleri yalnız geçmiş REAL_SIGNAL örnekleri maliyet sonrası eşikleri geçerse açılır.

### Big Move
- Shadow/sanal kalır; yeterli kapanmış rota kanıtı oluşmadan canlı Telegram'a dönmez.

## Maliyet modeli

`profitability_engine.py` varsayılan olarak yapılandırılabilir bir execution-cost modeli kullanır:
- fee/side: `PROFIT_FEE_RATE_PER_SIDE` (varsayılan 0.0005)
- slippage reserve/side: `PROFIT_SLIPPAGE_RATE_PER_SIDE` (varsayılan 0.0001)
- funding reserve: `PROFIT_FUNDING_RESERVE_RATE` (varsayılan 0)

Gerçek hesap/tier ücretleri farklıysa environment değişkenleriyle güncellenebilir. Slippage değeri borsa ücreti değil, konservatif modelleme rezervidir.

## Canlıya çıkma eşikleri

Varsayılan Profit Mode V2 eşikleri:
- minimum örnek: 20
- minimum ortalama Net R: +0.03R
- minimum maliyet-sonrası profit factor: 1.10
- maksimum stop oranı: %32
- TP1→BE senaryosunda maliyet sonrası minimum +0.05R

Bu değerler `profitability_engine.py` üzerinden environment değişkenleriyle ayarlanabilir; performans kanıtı olmadan gevşetilmemelidir.

## Ana raporlar

- `profit_mode_report.json` — Premium LONG/SHORT, Scalp TEPKI ve Pump maliyet-sonrası profil
- `profit_mode_rejections.json` — canlıya çıkmayan Premium adayları ve nedenleri
- `trade_ledger.json` — Premium geçmiş + maliyet sonrası Net R alanları
- `scalp_performance_ledger.json` — Scalp sanal/gerçek teknik sonuçları
- `pump_performance_ledger.json` — Pump/Dump sonuçları

## Telegram ilkesi

Telegram'a mümkün olduğunca yalnız kanıtlanmış gerçek giriş ve mevcut açık işlemlerin TP/BE/SL sonuçları gider. PREWATCH, EARLY, ham impuls ve kanıtlanmamış setup'lar kullanıcıya işlem olarak sunulmaz.

## Robotlaştırma

Otomatik emir aşamasına geçiş için sistemin önce gerçekçi maliyet modeliyle yeterli örnekte pozitif Net R, kabul edilebilir drawdown ve setup bazında tutarlı performans göstermesi gerekir. Bu kanıt oluşmadan otomatik OKX emirleri eklenmez.

**Temel ilke: önce maliyet sonrası edge, sonra canlı sinyal; edge yoksa işlem yok.**
