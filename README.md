# Kripto Sinyal Analiz Sistemi

OKX USDT perpetual futures piyasasını analiz eden, işlem adaylarını Telegram üzerinden bildiren ve sonuçları ölçen çoklu radar sistemidir.

> Sistem otomatik emir açmaz. İşlemler kullanıcı tarafından manuel açılır. Varsayılan yaklaşım düşük kaldıraç, Isolated marjin ve zorunlu stoptur.

## Güncel Sistem Özeti

**Bakım tabanı — 18 Ağustos 2026:** Trading çekirdeği korunur; panelin aktif ürün zinciri V3.32.9'dur. Öncelik yeni özellik değil, veri büyütme, ledger sağlığı, TP3 runner ölçümü ve gerçek telefon/masaüstü sürtünmelerinin giderilmesidir. Ayrıntılı politika: `docs/system-maintenance-v3329.md`.

Canlı Telegram işlem sinyali üreten üç bileşen vardır:

1. **Premium MTF Futures Bot** — ana ve öncelikli işlem sinyalleri
2. **Hızlı Scalp Radar** — kısa vadeli hızlı hareketler
3. **Erken Pump/Dump Radarı** — ani hacim ve kırılım hareketleri

Yeni fikirler doğrudan canlı sisteme eklenmez. Önce Telegram ve emir kapalı gölge sistemlerde ölçülür.

### Emekliye Ayrılan Sistem

Eski **Swing Radar V3** tamamen kaldırılmıştır. Yeni işlem açmaz, eski işlem takip etmez ve Telegram göndermez. Eski workflow, Python kodu, state, ledger ve Telegram teslim kayıtları repodan temizlenmiştir.

Yerine yalnız gölge modunda çalışan **Swing Shadow V4** bulunmaktadır.

## Şifreli Canlı Kripto Kontrol Paneli

**Kripto Kontrol Paneli**, özel repodaki gerçek state ve ledger kayıtlarını şifreli bir web ekranında birleştirir.

- Aktif ürün zinciri: **V3.32.9**
- Sabit giriş noktası: `dashboard_app.py`
- Aktif runtime: `dashboard_share_runtime_app.py`
- Premium, Scalp, Pump/Dump ve Yeni Liste açık işlemleri
- Giriş, TP1/TP2/TP3, SL ve hedef ilerlemesi
- Kapanmış TP/SL/BE sonuçları ve kesin kaydı bulunan Net R
- System Control genel sağlığı ve bileşen bazında teknik durum
- Tarayıcı açıkken 30 saniyede bir canlı veri yenileme
- Şifreli oturum, giriş denemesi sınırı ve güvenlik başlıkları
- Özel GitHub verisini yalnız sunucunun okuması; erişim anahtarının tarayıcıya gönderilmemesi
- Mobil uyumlu, koyu ve özgün arayüz

Panel botlardan ayrı ve **salt okunur** çalışır. Telegram akışını, sinyal üretimini, TP/SL takibini veya strateji kurallarını değiştirmez. Otomatik emir açmaz; kullanıcı parası tutmaz; borsa hesabı yönetmez ve kazanç garantisi vermez.

V3.32.9 zincirinde V3.32.6 yüzey/parite koruması, V3.32.7 kullanıcı yolculuğu ve hesap güvenliği, V3.32.8 hesaba bağlı İzleme Listesi, V3.32.9 paylaşılabilir gerçek işlem kartları birlikte korunur. Ayrıntılı mimari ve kurulum için `dashboard/README.md` okunmalıdır.

Panel artık **stabilite modundadır**: gerçek telefon/masaüstü kullanımında doğrulanmış sürtünme, hata veya gereksiz karmaşa yoksa yeni özellik eklenmez. Panel değişikliği trading çekirdeğinden ayrı tutulur.

Dağıtım ayarları `Dockerfile.dashboard` ve `render.yaml` dosyalarındadır. Actions artifact'ı yalnız acil durum için üretilen statik yedektir; canlı panel değildir.

İlk hedef paneli 30–60 gün yalnız kendi kullanımımızda doğrulamaktır. Performans kayıtları güvenilir ve sistem kararlı görülmeden ücretli beta açılmaz. Ücretli sunumdan önce ücretli sinyal ve yatırım danışmanlığı yönünden hukukçu görüşü alınır.

## 1. Premium MTF Futures Bot

Ana işlem sinyali sistemidir.

### Analiz

- 4H ana trend
- 1H yön onayı
- 15M işlem kurulumu
- 5M erken dönüş ve giriş kontrolü
- LONG ve SHORT analizi
- Geç veya hedefe yaklaşmış giriş engeli
- Hacim, momentum, RSI, ADX ve ATR kontrolleri
- Market koruma ve risk modu
- Açık sinyal limiti ve duplicate koruması

### Takip

- TP1, TP2, TP3, SL ve break-even takibi
- İşlem başına Net R
- Stop sonrası 30–240 dakika takip
- Süre sonu sonrası sessiz takip
- Kök neden ve işlem kalitesi teşhisi
- Strateji, config, build ve Git sürüm kaydı

### Ana Dosyalar

- `main.py`
- `strategy.py`
- `config.py`
- `open_signals.json`
- `performance.json`
- `trade_ledger.json`
- `.github/workflows/main.yml`

## 2. Hızlı Scalp Radar

- 1M ve 5M zaman dilimleri
- Hacim patlaması ve kısa vadeli momentum
- Mum kapanış gücü ve RSI uygunluğu
- Girişe yakınlık ve duplicate kontrolü
- TP/SL ve performans takibi
- PREWATCH/EARLY adaylarını Telegram'a göndermeden sessiz kaydetme
- Yalnız gerçek işlem sinyallerini Telegram'a gönderme

### Ana Dosyalar

- `scalp_radar.py`
- `scalp_radar_state.json`
- `scalp_performance_ledger.json`
- `.github/workflows/scalp-radar.yml`

## 3. Erken Pump/Dump Radarı

- 1M, 5M ve 15M hareket uyumu
- Hacim, kırılım, momentum ve giriş sapması kontrolü
- LONG pump ve SHORT dump analizi
- Fake breakout ve uzamış giriş teşhisi
- Stop sonrası sessiz takip
- Gerçek sinyal mantığını değiştirmeyen shadow trend gözlemi

### Ana Dosyalar

- `pump_radar.py`
- `pump_radar_state.json`
- `pump_performance_ledger.json`
- `.github/workflows/pump-radar.yml`

## 4. Swing Shadow V4

Uzun vadeli Swing yaklaşımını sıfırdan ölçen gölge sistemdir.

- 1D piyasa rejimi
- 4H trend ve hacim
- 1H pullback/reclaim
- 15M tetik
- En likit 60 OKX swap paritesini tarama
- En yakın 12 aday ve elenme nedenleri
- LONG/SHORT yön dengesi
- Sanal TP/SL ve maliyet sonrası Net R
- Telegram kapalı
- Otomatik emir kapalı
- Canlı kuralları değiştirme kapalı

Canlı adaylık için en az 30 kapanmış sanal işlem ve bütün başarı kapılarının geçilmesi gerekir.

### Ana Dosyalar

- `swing_shadow_v4.py`
- `swing_shadow_v4_ledger.json`
- `test_swing_shadow_v4.py`
- `.github/workflows/swing-shadow-v4.yml`

## Analiz ve Güvenlik Katmanları

### Portföy Risk

`portfolio_risk.py`, Premium, Scalp, Pump/Dump ve Yeni Liste açık sinyallerini birlikte kontrol eder.

- Aynı coin aynı yön çakışması
- Aynı coin ters yön çakışması
- Yön yoğunluğu
- Toplam portföy yoğunluğu
- BLOCK/ALLOW sonuçlarının gölge performans takibi
- Her canlı bot için ayrı portföy gölge ledger'ı; saatlik tekil birleştirme
- Eşzamanlı workflow yazmalarına karşı dosya çakışma koruması

Swing Shadow V4 sanal olduğu için canlı portföy risk hesabına eklenmez.

### Decision Engine

`decision_engine.py` performans kayıtlarını salt okunur biçimde değerlendirir.

- KORU
- İZLE
- GÖLGEDE TUT
- YENİDEN TASARLA
- CANLI ADAYI

Kararlar otomatik uygulanmaz.

### Prescription Engine

`prescription_engine.py` olası kural değişikliklerini geçmiş veri ve holdout karşılaştırmasıyla inceler. Strateji, TP/SL veya filtreleri otomatik değiştirmez.

### System Control Center

`system_control_center.py` workflow, dosya, JSON, veri güncelliği ve repo kökündeki tüm JSON dosyalarının büyüklüğünü kontrol eder. Dosya başına 4 MB üzerinde sarı, 8 MB üzerinde kırmızı sağlık uyarısı üretir. Telegram yalnız genel sağlık RED olduğunda ve aynı hata için 12 saatlik tekrar engeliyle kullanılır. Teknik sağlık ile işlem performansını birbirinden ayırır.

Ledger arşivleme ilk aşamada otomatik değildir. 4 MB sonrası hazırlık, 6 MB sonrası manuel arşiv hazırlığı ve 8 MB öncesi zorunlu müdahale politikası `docs/system-maintenance-v3329.md` içinde tanımlanmıştır. Açık/sonuçlanmamış kayıtlar arşivlenmez veya silinmez.

### Diğer Gölge Katmanları

- Position Trend Shadow
- All Market Shadow
- Momentum Shadow
- Range Shadow
- Portfolio Risk Outcome Shadow
- Post-Result Shadow V1/V2/V3

Bu katmanlar Telegram sinyali veya emir üretmez.

## İşlem Sonrası Gölge Analizi

Post-Result Shadow, Premium işlemlerindeki TP1/TP2/TP3 sonrası alternatif yönetimleri karşılaştırır.

- TP1 sonrası break-even zamanlaması
- TP2 sonrası koruma
- TP3 sonrası runner
- Ek Net R
- Pozitif, sıfır ve negatif fark oranları

**TP3 runner**, V3.32.9 bakım döneminin resmi takip metriklerinden biridir. `post_result_shadow_v3_report.json` içindeki örnek sayısı, ortalama/toplam ek Net R ve pozitif/sıfır/negatif fark oranları ileri örnekte izlenir. `COMPARE_ONLY` kararı canlı TP3/BE kuralını değiştirmez.

Yeterli örnek ve ileri dönem doğrulaması olmadan canlı TP/BE kuralı değiştirilmez.

## GitHub Actions

| Sistem | Workflow | Çalışma |
|---|---|---|
| Premium MTF | `.github/workflows/main.yml` | Her 5 dakika |
| Pump/Dump | `.github/workflows/pump-radar.yml` | Premium'dan farklı dakikalar |
| Scalp | `.github/workflows/scalp-radar.yml` | Premium'dan farklı dakikalar |
| Swing Shadow V4 | `.github/workflows/swing-shadow-v4.yml` | Saatte bir |
| Decision Engine | `.github/workflows/decision-engine.yml` | Zamanlanmış veya manuel |
| System Control Center | `.github/workflows/system-control-center.yml` | Zamanlanmış veya manuel |
| Kripto Panel Kontrolü | `.github/workflows/crypto-dashboard.yml` | Kod değişikliği, PR veya manuel; canlı katman testi |
| Çekirdek testler | `.github/workflows/tests.yml` | Kod değişikliği, PR veya manuel |

## Telegram Politikası

Telegram'a yalnız işlem yapılabilir canlı sinyaller ve gerekli sonuç bildirimleri gönderilir.

- Premium: açık
- Scalp: açık
- Pump/Dump: açık
- Swing Shadow V4: kapalı
- Diğer gölge ve analiz sistemleri: kapalı
- System Control Center: yalnız kritik RED teknik uyarısı; aynı hata 12 saat içinde tekrarlanmaz

## Kurulum ve Test

Python sürümü: **3.11**

Bağımlılıklar:

```bash
python -m pip install --prefer-binary -r requirements.txt
python -m pip install "pytest>=8,<10"
```

Çekirdek testler:

```bash
for test_file in test_*.py; do python -m pytest -q "$test_file"; done
```

Ana bileşenleri manuel çalıştırma:

```bash
python main.py
python scalp_radar.py
python pump_radar.py
python swing_shadow_v4.py
python decision_engine.py
python system_control_center.py
python dashboard_app.py --root .
# Acil durum statik yedeği:
python dashboard_builder.py --root . --output dashboard_output/index.html
```

## GitHub Secrets

Canlı Telegram bileşenleri için:

- `TOKEN`
- `CHAT_ID`

Canlı panel sunucusu için:

- `GITHUB_PANEL_TOKEN` — özel repoya yalnız okuma yetkili erişim anahtarı
- `PANEL_PASSWORD_HASH` — PBKDF2 ile üretilmiş panel parola özeti

Panel sırları barındırma hizmetinin gizli ortam değişkenlerinde tutulur; GitHub'a commit edilmez ve sohbet içinde paylaşılmaz.

Token ve kimlik bilgileri Python, JSON, YAML, README veya Actions loglarına düz metin yazılmamalıdır. Sistem OKX emir API anahtarı kullanmaz.

## Veri ve Değişiklik Politikası

- State/ledger dosyaları gerçek takip verisidir; açık işlem varken elle temizlenmez.
- Tek veya birkaç işleme göre canlı filtre değiştirilmez.
- Yeni fikir önce gölge testte ölçülür.
- Premium'un çalışan ana giriş profili korunur.
- Değişiklikten sonra çekirdek test ve ilgili workflow yeşil doğrulanır.
- Gölge sonuçları canlı sisteme otomatik uygulanmaz.
- Ledger arşivleme önce manuel, ölçülebilir ve geri alınabilir tek çevrimle doğrulanır; sıcak ledger şeması aynı anda değiştirilmez.
- Panelde yeni özellik varsayılan olarak bekletilir; önce gerçek mobil/masaüstü sürtünmesi, hata ve gereksiz karmaşa çözülür.

## Bakım Belgeleri

- `docs/system-maintenance-v3329.md` — çekirdek dondurma sınırı, veri toplama, TP3 runner takibi, ledger arşivleme ve panel stabilite modu
- `docs/panel-journey-audit-v3327.md` — uçtan uca kullanıcı akışı
- `docs/panel-watchlist-sync-v3328.md` — hesap tabanlı cihazlar arası İzleme Listesi
- `docs/panel-share-cards-v3329.md` — paylaşılabilir işlem kartları

## Güvenlik

- Otomatik emir yoktur.
- Telegram sırları yalnız GitHub Secrets içinde tutulur.
- JSON dosyaları atomik yazılır.
- Ortak portföy gölge kayıtları bot başına ayrılır ve saatlik tek yazıcıyla birleştirilir.
- Kök dizindeki tüm JSON dosyaları bozulma ve büyüme açısından System Control Center tarafından izlenir.
- Workflow'larda concurrency, timeout ve güvenli push kullanılır.
- Repo özel tutuluyorsa erişim ve Actions kotası düzenli kontrol edilmelidir.

## Uyarı

Bu sistem finansal tavsiye değildir ve kâr garantisi vermez. Kripto futures işlemleri yüksek risklidir. Stop kullanmadan, yüksek kaldıraçla veya kaybetmeyi göze alamayacağınız parayla işlem açmayın.
