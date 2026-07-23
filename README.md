# Kripto Sinyal Analiz Sistemi

OKX USDT perpetual futures piyasasını analiz eden ve sonuçları Telegram'a gönderen çoklu radar sistemidir.

Sistem otomatik emir açmaz. Yalnızca piyasa verisini analiz eder, işlem adaylarını bildirir ve gönderilen sinyallerin TP/SL sonuçlarını takip eder.

## Aktif Sistemler

### 1. Premium MTF Futures Bot

Ana işlem sinyali sistemidir.

- 4H ana trend
- 1H yön onayı
- 15M işlem kurulumu
- 5M erken dönüş ve giriş kontrolü
- LONG ve SHORT analizi
- Geç giriş engeli
- Market koruma
- Risk modu
- TP1, TP2, TP3 ve stop takibi
- TP1 sonrası kalan işlemi girişten kapatma takibi

Dosyalar:

- `main.py`
- `strategy.py`
- `config.py`
- `.github/workflows/main.yml`

Workflow yaklaşık her 5 dakikada çalışır.

### 2. Hızlı Scalp Radar v2

Kısa süreli ve hızlı hareketleri tarar.

- 1M ve 5M hacim onayı
- Kısa vadeli momentum
- Girişe yakınlık kontrolü
- Maksimum açık sinyal sınırı
- Duplicate koruması
- TP/SL takibi

Dosyalar:

- `scalp_radar.py`
- `scalp_radar_state.json`
- `.github/workflows/scalp-radar.yml`

### 3. Erken Pump/Dump Radar v2

Ani hacim ve fiyat hareketlerini yakalamaya çalışır.

LONG için:

- 1M, 5M ve 15M hareket uyumu
- 1M ve 5M hacim onayı
- Direnç kırılımı
- Giriş sapması kontrolü

SHORT için:

- 1M, 5M ve 15M düşüş uyumu
- 1M ve 5M hacim onayı
- Destek kırılımı
- Giriş sapması kontrolü

Dosyalar:

- `pump_radar.py`
- `pump_radar_state.json`
- `.github/workflows/pump-radar.yml`

### 4. Swing Radar v2

Daha uzun süreli işlemler için çalışır.

- 1D ana trend
- 4H yapı ve trend
- 1H giriş onayı
- Maksimum %3 stop mesafesi
- Kalite, düşük risk, ADX ve hacme göre sıralama
- Tek çalışmada en fazla 1 yeni sinyal
- En fazla 3 açık Swing sinyali
- TP/SL takibi

Dosyalar:

- `swing_radar.py`
- `swing_radar_state.json`
- `.github/workflows/swing-radar.yml`

Swing workflow yaklaşık 2 saatte bir çalışır.

### 5. Coin Analyzer

Belirli coinlerin ayrıntılı teknik analizini yapmak için kullanılan yardımcı sistemdir.

Dosyalar:

- `coin_analyzer.py`
- `.github/workflows/coin-analysis.yml`

## Telegram Bildirimleri

Sistem şartlar tamamlandığında aşağıdaki bilgileri gönderebilir:

- Coin
- LONG veya SHORT yönü
- Giriş fiyatı
- Giriş bölgesi
- TP1, TP2 ve TP3
- Stop fiyatı
- Stop mesafesi
- Kalite skoru
- Hacim, RSI ve ADX verileri
- Güncel fiyat ve giriş sapması
- TP/SL sonuçları

## İşlem Kuralları

- Stop mutlaka kullanılmalıdır.
- Marjin tercihi `Isolated` olmalıdır.
- Kaldıraç düşük tutulmalıdır.
- Fiyat sinyal girişinden fazla uzaklaştıysa işlem açılmamalıdır.
- TP1 gelince varsayılan yaklaşım:
  - Pozisyonun yaklaşık %50'sinde kâr almak
  - Kalan işlemin stopunu giriş fiyatına çekmek
- Grafik kontrol edilmeden yalnızca Telegram mesajına göre işlem açılmamalıdır.

## Kurulum

Gerekli Python sürümü:

```text
Python 3.11
```

Bağımlılıkları kurmak için:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## GitHub Secrets

Repository ayarlarında aşağıdaki Actions Secrets bulunmalıdır:

```text
TOKEN
CHAT_ID
```

Bu değerler hiçbir Python, JSON, YAML veya README dosyasına düz metin olarak yazılmamalıdır.

## Bağımlılık Sürümleri

`requirements.txt` dosyasındaki paket sürümleri sabitlenmiştir.

Amaç:

- Her GitHub Actions çalışmasında aynı ortamı kurmak
- Yeni paket sürümünün botu habersiz bozmasını önlemek
- Hatalı bir güncellemede kolay geri dönüş sağlamak

Sürümler tek tek rastgele yükseltilmemelidir. Yeni sürüm kullanılmadan önce bütün botlar test edilmelidir.

## State ve Performans Dosyaları

Botlar açık sinyalleri ve sonuçları JSON dosyalarında tutar:

- `open_signals.json`
- `performance.json`
- `last_signals.json`
- `scalp_radar_state.json`
- `pump_radar_state.json`
- `swing_radar_state.json`

Bu dosyalar silinirse açık sinyal takibi ve geçmiş veriler kaybolabilir.

Gerçek açık işlemler varken state dosyalarını elle temizlemeyin.

## Güvenlik

- Telegram tokenı yalnızca GitHub Secrets içinde tutulmalıdır.
- OKX API anahtarı bu sistem için gerekli değildir.
- Sistem otomatik alım-satım emri açmaz.
- Actions loglarında Telegram yanıt gövdesi yazdırılmaz; yalnızca HTTP durum kodu gösterilir.
- Public repoda kaynak kodu ve state JSON verileri herkes tarafından görülebilir.
- Repository private yapılacaksa GitHub Actions dakika kotası kontrol edilmelidir.
- Token geçmişte yanlışlıkla commit edildiyse dosyadan silmek yetmez; token yenilenmelidir.

## Workflow Güvenliği

Ana workflow'larda:

- `concurrency` koruması
- `cancel-in-progress: false`
- Çalışma zaman aşımı
- Güvenli `git pull --rebase`
- Üç denemeli state push
- Push başarısız olursa kırmızı workflow sonucu

kullanılır.

## Önemli Uyarı

Bu sistem finansal tavsiye değildir ve kâr garantisi vermez.

Kripto futures işlemleri yüksek risklidir. Stop kullanmadan, yüksek kaldıraçla veya kaybetmeyi göze alamayacağınız parayla işlem açmayın.
