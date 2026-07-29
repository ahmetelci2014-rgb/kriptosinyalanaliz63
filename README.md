Kripto Sinyal Analiz Sistemi

OKX USDT perpetual futures piyasasını analiz eden ve uygun koşullarda Telegram bildirimi gönderen çoklu radar sistemidir.

Sistem otomatik emir açmaz. Piyasa verilerini analiz eder, işlem adaylarını bildirir, gönderilen sinyallerin TP/SL sonuçlarını takip eder ve performans kayıtlarını GitHub üzerindeki JSON dosyalarında saklar.

Aktif Sistemler

1. Premium MTF Futures Bot

Ana işlem sinyali sistemidir.

4H ana trend analizi

1H yön onayı

15M işlem kurulumu

5M erken dönüş ve giriş kontrolü

LONG ve SHORT analizi

Geç veya uzak giriş engeli

Market koruması

Risk modu

A kalite işlem önceliği

TP1, TP2, TP3 ve stop takibi

TP1 sonrası kalan pozisyonun giriş seviyesinden kapanma takibi

Stop sonrası 240 dakika hedefe dönüş takibi

Süre sonrası 24 saat teşhis takibi

Günlük Net R ve kök neden raporu

Strateji, config ve commit sürüm kaydı

Dosyalar:

main.py

strategy.py

config.py

open_signals.json

performance.json

last_signals.json

trade_ledger.json

.github/workflows/main.yml

Workflow yaklaşık her 5 dakikada çalışır.

2. Hızlı Scalp Radar

Kısa süreli ve hızlı hareketleri tarar.

1M ve 5M piyasa verisi

Kısa vadeli hacim ve momentum onayı

Girişe yakınlık kontrolü

Geç giriş engeli

Maksimum açık sinyal sınırı

Duplicate koruması

Ortak portföy çakışma kontrolü

TP/SL ve performans takibi

Sessiz ön izleme ve teşhis kayıtları

Dosyalar:

scalp_radar.py

scalp_radar_state.json

scalp_performance_ledger.json

.github/workflows/scalp-radar.yml

Workflow yaklaşık her 5 dakikada çalışır.

3. Erken Pump/Dump Radarı

Ani hacim ve fiyat hareketlerini yakalamaya çalışır.

LONG için:

Kısa vadeli yükseliş uyumu

1M ve 5M hacim onayı

Direnç kırılımı

Giriş sapması kontrolü

Destek koruması

SHORT için:

Kısa vadeli düşüş uyumu

1M ve 5M hacim onayı

Destek kırılımı

Giriş sapması kontrolü

Direnç koruması

Ek özellikler:

Ortak portföy çakışma kontrolü

TP/SL ve performans takibi

Shadow hareket ve teşhis kayıtları

Tek çalışmada sınırlı sayıda yeni sinyal

Dosyalar:

pump_radar.py

pump_radar_state.json

pump_performance_ledger.json

.github/workflows/pump-radar.yml

Workflow yaklaşık her 5 dakikada çalışır.

4. Swing Radar

Daha uzun süreli işlem fırsatları için çalışır.

4H ve 1H trend/yön onayı

15M erken giriş yolu

1H onaylı giriş yolu

SWING_EARLY_LONG/SHORT etiketleri

SWING_CONFIRM_LONG/SHORT etiketleri

Maksimum stop mesafesi kontrolü

Kalite, risk, ADX ve hacme göre sıralama

Tek çalışmada en fazla 1 yeni sinyal

Maksimum açık Swing sinyali sınırı

Ortak portföy çakışma kontrolü

TP/SL, teşhis ve performans takibi

Dosyalar:

swing_radar.py

swing_radar_state.json

swing_performance_ledger.json

.github/workflows/swing-radar.yml

Workflow her saat 12 ve 42 geçe, yaklaşık 30 dakikada bir çalışır.

5. Yeni Liste Fırsat Radarı

Yalnız son 72 saat içinde açılmış uygun OKX USDT perpetual marketlerini tarar.

Yeni listelenen marketleri otomatik keşfeder

En az 20 dakikalık veri oluşmasını bekler

Fırsat adaylarını Telegram'a göndermeden sessizce takip eder

Minimum 2 dakika sessiz doğrulama uygular

Kırılım veya kırılan seviyede retest/tutunma arar

1M, 3M ve 5M hacim onayı kullanır

Momentum ve mum kapanış gücünü kontrol eder

Geç girişleri engeller

Şartlar tamamlanınca giriş bölgesi, TP1, TP2, TP3 ve SL içeren bildirim gönderir

Bir çalışmada en fazla 1 giriş onayı gönderir

Ortak portföy çakışma kontrolünü kullanır

Eski “İZLE” kayıtlarını açık işlem olarak saymaz

CONFIRMED_TRADE + TRACKING kayıtlarını açık risk olarak kabul eder

FINAL kayıtlarını kapalı kabul eder

Dosyalar:

new_listing_radar.py

new_listing_radar_state.json

new_listing_performance_ledger.json

.github/workflows/new-listing-radar.yml

GitHub cron yaklaşık her 5 dakikada yeni bir çalışma başlatır. Zamanlanmış çalışmada aynı runner içinde yaklaşık 0, 60, 120 ve 180. saniyelerde dört kontrol yapılır. Manuel Run workflow işleminde tek kontrol yapılır.

6. Coin Analyzer

Belirli coinlerin ayrıntılı teknik analizini yapmak için kullanılan yardımcı sistemdir.

Dosyalar:

coin_analyzer.py

.github/workflows/coin-analysis.yml

Ortak Portföy Riski

portfolio_risk.py, yeni bir sinyal gönderilmeden önce aşağıdaki aktif sistemlerin açık kayıtlarını kontrol eder:

Premium MTF

Scalp

Pump/Dump

Swing

Yeni Liste

Temel korumalar:

Aynı coin ve aynı yönde ikinci sinyali engelleme

Aynı coin ve ters yönde çakışan sinyali engelleme

Toplam açık sinyal yoğunluğu uyarısı

Aynı yöndeki portföy yoğunluğu uyarısı

TP1 görmüş açık sinyali yarım risk olarak değerlendirme

Dosya:

portfolio_risk.py

Telegram Bildirimleri

Şartlar tamamlandığında mesajlarda aşağıdaki bilgiler bulunabilir:

Coin

LONG veya SHORT yönü

Sinyal/radar etiketi

Giriş fiyatı veya giriş bölgesi

TP1, TP2 ve TP3

Stop fiyatı

Stop mesafesi

Kalite veya onay skoru

Hacim, RSI, ADX ve trend verileri

Güncel fiyat ve giriş sapması

Risk/getiri bilgisi

TP/SL ve kapanış sonuçları

Stop ve süre sonrası teşhisler

İşlem Kuralları

Stop mutlaka kullanılmalıdır.

Marjin tercihi Isolated olmalıdır.

Kaldıraç düşük tutulmalıdır.

Fiyat sinyal girişinden fazla uzaklaşmışsa işlem açılmamalıdır.

TP1 geldiğinde varsayılan yaklaşım:

Pozisyonun yaklaşık %50'sinde kâr almak

Kalan pozisyonun stopunu giriş fiyatına çekmek

Grafik kontrol edilmeden yalnızca Telegram mesajına göre işlem açılmamalıdır.

Yeni listelenen ürünlerde fitil, slippage ve likidasyon riski daha yüksektir.

Sistem kâr garantisi vermez.

Otomatik Testler

Merkezî test workflow'u aktif Python dosyalarını derler ve çekirdek testleri çalıştırır.

Kontrol edilen başlıca dosyalar:

main.py

strategy.py

config.py

portfolio_risk.py

scalp_radar.py

pump_radar.py

swing_radar.py

new_listing_radar.py

coin_analyzer.py

test_main_core.py

Çalıştırılan testler:

Python sözdizimi/derleme kontrolü

Ana bot çekirdek testleri

Portföy riski testleri

Yeni Liste Radar self-testleri

Workflow:

.github/workflows/tests.yml

Test workflow'u Telegram tokenını kullanmaz ve Telegram mesajı göndermez.

Kurulum

Gerekli Python sürümü:

Python 3.11

Bağımlılıkları kurmak için:

python -m pip install --prefer-binary -r requirements.txt

requirements.txt içindeki paket sürümleri sabitlenmiştir. Paket sürümleri test edilmeden rastgele yükseltilmemelidir.

GitHub Secrets

Repository ayarlarında aşağıdaki Actions Secrets bulunmalıdır:

TOKEN
CHAT_ID

Bu değerler hiçbir Python, JSON, YAML veya README dosyasına düz metin olarak yazılmamalıdır.

State ve Performans Dosyaları

Ana bot:

open_signals.json

performance.json

last_signals.json

trade_ledger.json

Scalp:

scalp_radar_state.json

scalp_performance_ledger.json

Pump/Dump:

pump_radar_state.json

pump_performance_ledger.json

Swing:

swing_radar_state.json

swing_performance_ledger.json

Yeni Liste:

new_listing_radar_state.json

new_listing_performance_ledger.json

Bu dosyalar silinirse açık sinyal takibi, performans geçmişi veya teşhis kayıtları kaybolabilir. Gerçek açık işlemler varken state ve ledger dosyaları elle temizlenmemelidir.

JSON kayıtları atomik yazma yöntemiyle güncellenir. Workflow'lar değişen state ve performans dosyalarını GitHub'a güvenli şekilde kaydetmeye çalışır.

Workflow Güvenliği

Ana çalışma workflow'larında:

concurrency koruması

cancel-in-progress: false

Çalışma zaman aşımı

Pip cache

Sabitlenmiş bağımlılık kurulumu

Güvenli git pull --rebase --autostash

Üç denemeli state push

Push başarısız olursa kırmızı workflow sonucu

kullanılır.

Test workflow'unda yalnız contents: read izni bulunur. State kaydeden bot workflow'larında contents: write izni bulunur.

Güvenlik

Telegram tokenı yalnızca GitHub Secrets içinde tutulmalıdır.

OKX API anahtarı bu sistem için gerekli değildir.

Sistem otomatik alım-satım emri açmaz.

Actions loglarında Telegram yanıt gövdesi yazdırılmaz; yalnız HTTP durum kodu gösterilir.

Public repoda kaynak kodu ve state JSON verileri herkes tarafından görülebilir.

Token geçmişte yanlışlıkla commit edildiyse dosyadan silmek yeterli değildir; token yenilenmelidir.

GitHub Secret Scanning, Dependency Graph ve Dependabot güvenlik özellikleri açık tutulabilir.

Ana dal için force push ve silme koruması kullanılmalıdır.

Önemli Uyarı

Bu sistem finansal tavsiye değildir ve kâr garantisi vermez.

Kripto futures işlemleri yüksek risklidir. Stop kullanmadan, yüksek kaldıraçla veya kaybetmeyi göze alamayacağınız parayla işlem açmayın.
