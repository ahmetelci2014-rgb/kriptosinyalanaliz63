# Kripto Sinyal Analiz Sistemi

OKX USDT perpetual futures piyasasını analiz eden, uygun işlem adaylarını Telegram üzerinden bildiren ve gönderilen sinyallerin sonuçlarını takip eden çoklu radar sistemidir.

> **Önemli:** Sistem otomatik emir açmaz. Alım-satım kararı ve emir girişi kullanıcı tarafından manuel olarak yapılır.

## Sistem Özeti

Aktif yapı dört ayrı sinyal motorundan oluşur:

1. **Premium MTF Futures Bot** — ana işlem sinyalleri
2. **Hızlı Scalp Radar** — kısa vadeli hızlı hareketler
3. **Erken Pump/Dump Radarı** — ani hacim ve kırılım hareketleri
4. **Swing Radar** — daha uzun süreli trend işlemleri

Dört bot:

- Aynı OKX futures piyasasını tarar.
- Kendi zaman dilimi ve filtreleriyle bağımsız aday üretir.
- Ortak portföy risk modülüyle birbirinin açık sinyallerini kontrol eder.
- Açık işlemleri ve performans sonuçlarını JSON dosyalarında takip eder.
- State ve ledger dosyalarını doğrulamalı atomik yazımla kaydeder.
- GitHub Actions üzerinden otomatik çalışır.
- Telegram üzerinden sinyal ve sonuç bildirimi gönderir.

## 1. Premium MTF Futures Bot

Ana işlem sinyali sistemidir.

### Analiz Yapısı

- 4H ana trend
- 1H yön onayı
- 15M işlem kurulumu
- 5M erken dönüş ve giriş kontrolü
- LONG ve SHORT analizi
- Geç veya hedefe yaklaşmış giriş engeli
- Hacim, momentum, RSI, ADX ve ATR kontrolleri
- Market koruma
- Risk modu
- Açık sinyal limiti
- Duplicate sinyal koruması

### İşlem Takibi

- TP1, TP2 ve TP3 takibi
- Stop takibi
- TP1 sonrası kalan pozisyonun giriş fiyatından kapanma takibi
- İşlem başına Net R hesabı
- Günlük Net R ve teşhis raporu
- Stop sonrası 30, 60, 120, 180 ve 240 dakika takip
- Stop sonrası TP1 bölgesine dönüş kontrolü
- Fitil/dar stop, erken giriş ve yanlış yön gibi kök neden sınıflandırması
- 18 saatlik işlem süresi sonrası 6, 12 ve 24 saatlik sessiz takip
- Strateji, config, bot build ve Git commit sürüm kaydı

### Ana Dosyalar

- `main.py`
- `strategy.py`
- `config.py`
- `portfolio_risk.py`
- `open_signals.json`
- `performance.json`
- `last_signals.json`
- `trade_ledger.json`
- `.github/workflows/main.yml`

### Çalışma Zamanı

```text
Her 5 dakikada bir
Cron: */5 * * * *
```

## 2. Hızlı Scalp Radar

Kısa süreli ve hızlı hareketleri tarar.

### Özellikler

- 1M ve 5M zaman dilimleri
- Hacim patlaması kontrolü
- Kısa vadeli momentum
- Mum kapanış gücü
- RSI uygunluğu
- Girişe yakınlık kontrolü
- Maksimum açık Scalp sinyali
- Duplicate sinyal koruması
- TP/SL ve performans takibi
- Stop sonrası hareket teşhisi
- PREWATCH ve EARLY adaylarını Telegram'a göndermeden sessiz kaydetme
- Yalnız gerçek ve işlem yapılabilir Scalp sinyallerini Telegram'a gönderme

### Ana Dosyalar

- `scalp_radar.py`
- `scalp_radar_state.json`
- `scalp_performance_ledger.json`
- `.github/workflows/scalp-radar.yml`

### Çalışma Zamanı

```text
Her 5 dakikada bir, Ana MTF'den farklı dakikalarda
Cron: 4-59/5 * * * *
```

## 3. Erken Pump/Dump Radarı

Ani fiyat, hacim ve kırılım hareketlerini yakalamaya çalışır.

### LONG Kontrolleri

- 1M, 5M ve 15M hareket uyumu
- 1M ve 5M hacim onayı
- Direnç kırılımı
- Giriş sapması
- Kırılım seviyesine uzaklık
- Momentum devamlılığı

### SHORT Kontrolleri

- 1M, 5M ve 15M düşüş uyumu
- 1M ve 5M hacim onayı
- Destek kırılımı
- Giriş sapması
- Kırılım seviyesine uzaklık
- Momentum devamlılığı

### Teşhis Yapısı

- Fake breakout kontrolü
- Geç veya uzamış giriş analizi
- Hacim ve momentum sönmesi
- Maksimum lehe ve aleyhe hareket takibi
- Stop sonrası 15, 30, 60, 120 ve 240 dakika takip
- Gerçek sinyal mantığını değiştirmeyen sessiz shadow trend gözlemi

### Ana Dosyalar

- `pump_radar.py`
- `pump_radar_state.json`
- `pump_performance_ledger.json`
- `.github/workflows/pump-radar.yml`

### Çalışma Zamanı

```text
Her 5 dakikada bir, diğer botlardan farklı dakikalarda
Cron: 2-59/5 * * * *
```

## 4. Swing Radar

Daha uzun süreli trend ve yapı işlemleri için çalışır.

### Analiz Yapısı

- 1D ana trend
- 4H trend ve piyasa yapısı
- 1H onaylı giriş yolu
- 15M erken giriş yolu
- Kalite, düşük risk, ADX ve hacme göre sıralama
- Giriş bölgesi kontrolü
- Son yön doğrulaması
- Tek çalışmada sınırlı yeni sinyal
- Maksimum açık Swing sinyali
- TP/SL ve performans takibi

### Swing Teşhisi

- 15M erken giriş ile 1H onaylı giriş karşılaştırması
- Stop sonrası 1 saat, 4 saat, 12 saat, 24 saat ve 48 saat takip
- Erken giriş, zayıf devam ve yanlış yön ayrımı

### Ana Dosyalar

- `swing_radar.py`
- `swing_radar_state.json`
- `swing_performance_ledger.json`
- `.github/workflows/swing-radar.yml`

### Çalışma Zamanı

```text
Saatte iki kez
Cron: 12,42 * * * *
```

## Ortak Portföy Risk Sistemi

Dört bot `portfolio_risk.py` üzerinden birbirinin açık sinyallerini kontrol eder.

Modül yalnız state dosyalarını okur; emir açmaz ve mevcut state dosyalarını değiştirmez.

### Sert Engeller

Yeni adayla aynı coinde başka açık sinyal varsa:

- Aynı yöndeki ikinci sinyal engellenir.
- Ters yöndeki çakışan sinyal engellenir.

Örnek:

```text
Scalp: ENAUSDT LONG açık
Ana MTF: ENAUSDT LONG adayı
Sonuç: Yeni Ana MTF sinyali gönderilmez
```

```text
Swing: BTCUSDT SHORT açık
Pump/Dump: BTCUSDT LONG adayı
Sonuç: Yön çatışması nedeniyle yeni sinyal gönderilmez
```

### Yoğunluk Uyarıları

Varsayılan risk ağırlıkları:

- TP1 görülmemiş açık sinyal: `1.0`
- TP1 görülmüş açık sinyal: `0.5`
- Aynı yön için uyarı seviyesi: `4.0`
- Toplam portföy için uyarı seviyesi: `8.0`

Bu sınırlar aşılırsa sistem sinyali otomatik olarak engellemek yerine Telegram mesajına portföy yoğunluk uyarısı ekler.

## Atomik JSON Koruması

Ana MTF, Scalp, Pump/Dump ve Swing state/ledger dosyalarını atomik olarak yazar.

Yazım sırası:

1. Veri aynı klasörde geçici dosyaya yazılır.
2. Dosya diske zorlanır.
3. JSON tekrar açılıp doğrulanır.
4. Geçici dosya `os.replace` ile tek adımda gerçek dosyanın yerine geçirilir.
5. Hata oluşursa eski sağlam JSON korunur ve geçici dosya temizlenir.

Bu yapı, workflow yarıda kesildiğinde veya GitHub runner kapanırken JSON dosyasının yarım yazılma riskini azaltır.

## Otomatik Test Sistemi

Çekirdek test workflow'u:

```text
.github/workflows/tests.yml
```

Test dosyası:

```text
test_main_core.py
```

Manuel çalıştırma:

```bash
python test_main_core.py -v
```

Mevcut test paketi 15 temel kontrol içerir:

- Atomik JSON yazımı
- LONG Net R hesabı
- SHORT Net R hesabı
- TP1 sonrası break-even Net R hesabı
- Tekrarlanan TP1/TP2 olay koruması
- Fitil/dar stop teşhisi
- Muhtemel erken giriş teşhisi
- Muhtemel yanlış yön teşhisi
- Stop takibi tamamlanmadan kesin teşhis verilmemesi
- Git commit ve build sürüm kaydı
- Aynı coin aynı yön portföy çakışması
- Aynı coin ters yön portföy çakışması
- TP1 görülmüş sinyalin yarım risk sayılması
- Yön yoğunluğu uyarısı
- Temel Python derleme kontrolü

Test sistemi:

- Piyasaya bağlanmaz.
- Telegram mesajı göndermez.
- Gerçek state dosyalarını değiştirmez.
- Python dosyaları değiştiğinde otomatik çalışır.
- Manuel olarak da çalıştırılabilir.

## GitHub Actions Workflow'ları

| Sistem | Workflow | Zamanlama |
|---|---|---|
| Premium MTF | `.github/workflows/main.yml` | Her 5 dakika |
| Pump/Dump | `.github/workflows/pump-radar.yml` | Dakika 2, 7, 12, 17... |
| Scalp | `.github/workflows/scalp-radar.yml` | Dakika 4, 9, 14, 19... |
| Swing | `.github/workflows/swing-radar.yml` | Her saat dakika 12 ve 42 |
| Çekirdek test | `.github/workflows/tests.yml` | Kod değişikliği, PR veya manuel |

Ana botlar aynı dakikada başlamayacak şekilde dağıtılmıştır. Bu yapı Git push çakışmalarını azaltmaya yardımcı olur.

## Workflow Güvenliği ve Hızlandırma

Aktif workflow'larda:

- `concurrency` koruması
- `cancel-in-progress: false`
- Çalışma zaman aşımı
- `actions/checkout@v4`
- `actions/setup-python@v5`
- Python 3.11
- `requirements.txt` tabanlı pip cache
- Gereksiz her-run `pip upgrade` işleminin kaldırılması
- `--prefer-binary` ile bağımlılık kurulumu
- `if: always()` ile state dosyalarını kaydetme
- Güvenli `git pull --rebase --autostash`
- Üç denemeli Git push
- Push başarısız olursa kırmızı workflow sonucu

kullanılır.

## Telegram Bildirimleri

Sistem şartlar tamamlandığında aşağıdaki bilgileri gönderebilir:

- Coin
- LONG veya SHORT yönü
- Giriş fiyatı veya giriş bölgesi
- TP1, TP2 ve TP3
- Stop fiyatı
- Stop mesafesi
- Kalite veya skor
- Hacim, RSI ve ADX bilgileri
- Güncel fiyat
- Giriş sapması
- Portföy yoğunluk uyarısı
- TP/SL sonuçları
- Net R sonucu
- Stop sonrası teşhis
- Günlük performans raporu

## Önerilen İşlem Disiplini

- Stop mutlaka kullanılmalıdır.
- Marjin tercihi `Isolated` olmalıdır.
- Kaldıraç düşük tutulmalıdır.
- Fiyat sinyal girişinden fazla uzaklaştıysa işlem açılmamalıdır.
- TP1 gerçekleştiğinde varsayılan yaklaşım:
  - Pozisyonun yaklaşık `%50` bölümünde kâr almak
  - Kalan pozisyonun stopunu giriş fiyatına çekmek
- Aynı anda çok sayıda aynı yönlü işlem açılmamalıdır.
- Grafik kontrol edilmeden yalnız Telegram mesajına göre işlem açılmamalıdır.

## Kurulum

Gerekli Python sürümü:

```text
Python 3.11
```

Bağımlılıkları kurmak için:

```bash
python -m pip install --prefer-binary -r requirements.txt
```

Ana botu manuel çalıştırmak için:

```bash
python main.py
```

Radarları manuel çalıştırmak için:

```bash
python scalp_radar.py
python pump_radar.py
python swing_radar.py
```

Testleri çalıştırmak için:

```bash
python test_main_core.py -v
```

## GitHub Secrets

Repository ayarlarında aşağıdaki Actions Secrets bulunmalıdır:

```text
TOKEN
CHAT_ID
```

Bu değerler Python, JSON, YAML veya README dosyalarına düz metin olarak yazılmamalıdır.

Sistem Telegram bot tokenı dışında OKX emir API anahtarı gerektirmez; çünkü otomatik emir açmaz.

## State ve Performans Dosyaları

### Ana MTF

- `open_signals.json`
- `performance.json`
- `last_signals.json`
- `trade_ledger.json`

### Scalp

- `scalp_radar_state.json`
- `scalp_performance_ledger.json`

### Pump/Dump

- `pump_radar_state.json`
- `pump_performance_ledger.json`

### Swing

- `swing_radar_state.json`
- `swing_performance_ledger.json`

Bu dosyalar silinirse açık sinyal takibi, sonuç geçmişi veya teşhis verileri kaybolabilir.

Gerçek açık işlemler varken state dosyalarını elle temizlemeyin.

## Sürüm ve İzlenebilirlik

Ana sistem yeni işlem kayıtlarında aşağıdaki bilgileri tutar:

- Bot build sürümü
- Strateji sürümü
- Config sürümü
- Git commit SHA
- GitHub workflow adı
- GitHub run ID
- GitHub run numarası
- Git branch/ref bilgisi

Bu kayıtlar, ileride hangi strateji sürümünün hangi sonucu ürettiğini karşılaştırmak için kullanılır.

## Dosya Yapısı

```text
.
├── main.py
├── strategy.py
├── config.py
├── portfolio_risk.py
├── scalp_radar.py
├── pump_radar.py
├── swing_radar.py
├── coin_analyzer.py
├── test_main_core.py
├── requirements.txt
├── open_signals.json
├── performance.json
├── last_signals.json
├── trade_ledger.json
├── scalp_radar_state.json
├── scalp_performance_ledger.json
├── pump_radar_state.json
├── pump_performance_ledger.json
├── swing_radar_state.json
├── swing_performance_ledger.json
└── .github/
    └── workflows/
        ├── main.yml
        ├── scalp-radar.yml
        ├── pump-radar.yml
        ├── swing-radar.yml
        └── tests.yml
```

Aktif workflow'lar `_radar.py` ile biten radar dosyalarını kullanır. Eski veya arşiv amaçlı benzer Python dosyaları varsa aktif sistemle karıştırılmamalıdır.

## Güvenlik

- Telegram tokenı yalnız GitHub Secrets içinde tutulmalıdır.
- Actions loglarında Telegram API yanıt gövdesi yazdırılmamalıdır.
- Sistem otomatik alım-satım emri açmaz.
- Public repodaki kaynak kodu ve state JSON verileri herkes tarafından görülebilir.
- State JSON dosyaları işlem geçmişi ve sinyal bilgisi içerebilir.
- Token geçmişte yanlışlıkla commit edildiyse yalnız dosyadan silmek yeterli değildir; token yenilenmelidir.
- Repository private yapılacaksa GitHub Actions kullanım kotası kontrol edilmelidir.

## Değişiklik Politikası

Strateji ayarları tek veya birkaç işleme göre değiştirilmemelidir.

Daha sağlıklı değerlendirme için:

- Yeterli sayıda kapanmış işlem
- En az birkaç günlük rapor
- Tekrarlayan aynı kök neden
- LONG/SHORT ve bot türüne göre ayrılmış sonuçlar
- Net R ve TP/SL dağılımı

birlikte değerlendirilmelidir.

Kod değişikliğinden sonra:

1. `Bot Core Tests` çalıştırılmalıdır.
2. İlgili bot workflow'u manuel çalıştırılmalıdır.
3. Her iki workflow'un da yeşil olduğu doğrulanmalıdır.
4. Telegram ve state kayıtları kontrol edilmelidir.

## Önemli Uyarı

Bu sistem finansal tavsiye değildir ve kâr garantisi vermez.

Kripto futures işlemleri yüksek risklidir. Stop kullanmadan, yüksek kaldıraçla veya kaybetmeyi göze alamayacağınız parayla işlem açmayın.
