# Sistem Bakım ve Stabilite Politikası — V3.32.9

Tarih: 18 Ağustos 2026

Bu belge V3.32.9 sonrası çalışma politikasını sabitler. Amaç yeni strateji veya panel özelliği eklemek değil; çalışan trading çekirdeğini korumak, ölçüm verisini büyütmek ve paneli gerçek kullanım sürtünmelerine göre sadeleştirmektir.

## 1. Değişiklik dondurma sınırı

Bu bakım döneminde aşağıdaki canlı trading davranışlarına dokunulmaz:

- `main.py`, `strategy.py`, `config.py` ana karar mantığı,
- Premium MTF sinyal üretimi,
- Scalp sinyal üretimi,
- Pump/Dump sinyal üretimi,
- giriş / TP1 / TP2 / TP3 / SL / BE davranışı,
- canlı risk filtreleri ve skor eşikleri,
- Telegram canlı sinyal ve sonuç akışı,
- state/ledger yazım semantiği.

Canlı çekirdekte değişiklik ancak yeterli gölge örneği, ileri dönem doğrulaması ve açık bir regresyon test planı varsa ayrıca değerlendirilir. Tek veya birkaç sonuca göre filtre değişikliği yapılmaz.

## 2. Veri toplama politikası

### Canlı kalan sistemler

- Premium MTF Futures Bot
- Hızlı Scalp Radar
- Erken Pump/Dump Radarı

### Gölge / ölçümde kalan sistemler

- Swing Shadow V4
- Portfolio Risk Outcome Shadow
- Post-Result Shadow V1/V2/V3
- diğer mevcut gölge analiz katmanları

Bu bakım paketi workflow zamanlamalarını, Telegram sınırlarını veya canlı/gölge ayrımını değiştirmez. Öncelik mevcut veri akışının kesintisiz büyümesidir.

## 3. TP3 runner resmi takip metriği

Post-Result Shadow V3, TP3 sonrası runner seçeneklerini canlı kuralı değiştirmeden karşılaştırmaya devam eder.

18 Ağustos 2026 08:35 Türkiye saati itibarıyla `post_result_shadow_v3_report.json` başlangıç referansı:

| Model | Örnek | Ortalama ek R | Toplam ek Net R | Pozitif | Sıfır | Negatif |
|---|---:|---:|---:|---:|---:|---:|
| `TP3_RUNNER_TRAIL_0_5R` | 50 | +0.1249R | +6.2430R | %20 | %80 | %0 |
| `TP3_RUNNER_TRAIL_1_0R` | 50 | +0.1476R | +7.3821R | %14 | %86 | %0 |

Bu tablo yalnız bir başlangıç fotoğrafıdır; canlı TP3 yönetimi için karar değildir. Takipte özellikle şu dört şey aranır:

1. örnek sayısının düzenli büyümesi,
2. ek Net R'nin yeni veride korunup korunmaması,
3. negatif fark oranının ortaya çıkıp çıkmaması,
4. sonuçların farklı piyasa rejimlerinde benzer kalıp kalmaması.

Post-Result kararı `COMPARE_ONLY` kaldığı sürece canlı TP3/BE kuralına otomatik aktarım yapılmaz.

## 4. Ledger büyümesi ve güvenli arşivleme planı

`system_control_center.py` mevcut politikada repo kökündeki JSON dosyalarını izler; dosya başına 4 MB üzerinde sarı, 8 MB üzerinde kırmızı sağlık uyarısı üretir.

Arşivleme ilk aşamada **otomatik yapılmaz**. Önce bir manuel ve geri alınabilir arşiv çevrimi doğrulanır.

### Tetik seviyeleri

- **< 4 MB:** yalnız izleme.
- **4–6 MB:** arşiv adayı olarak işaretle; şema, kayıt sayısı ve tarih aralığını raporla.
- **6–8 MB:** manuel arşiv hazırlığı zorunlu; aktif/açık kayıtların ayrımı doğrulanır.
- **>= 8 MB:** teknik sağlık kırmızı kabul edilir; yeni özellik işinden önce arşiv/boyut müdahalesi yapılır.

### Güvenli arşiv kuralı

1. Açık veya henüz sonucu kesinleşmemiş kayıtlar sıcak ledger'da kalır.
2. Yalnız kapanmış ve sonucu kesinleşmiş eski kayıtlar arşiv adayına alınır.
3. Arşiv öncesi kaynak dosyanın SHA256 özeti, toplam kayıt sayısı, açık/kapalı dağılımı ve zaman aralığı kaydedilir.
4. Arşiv dosyaları dönem bazlı ve salt okunur tutulur; örnek yol: `ledger_archives/<ledger>/<YYYY-MM>.json`.
5. Sıcak ledger geçerli JSON olarak kalır; okuyucu kodların beklediği üst seviye şema değiştirilmez.
6. Arşiv sonrası toplam kayıt eşitliği, kapanmış işlem toplamları, Net R toplamları ve son kayıt kimliği eski snapshot ile karşılaştırılır.
7. Dashboard, Decision Engine, Prescription Engine, System Control Center ve ilgili testler yeni sıcak ledger ile doğrulanır.
8. İlk manuel arşiv çevrimi sorunsuz geçmeden otomatik silme/taşıma workflow'u kurulmaz.
9. Her arşiv değişikliği tek başına geri alınabilir commit olarak yapılır.

### Kesin yasaklar

- Açık işlem varken ledger'ı elle sıfırlamak,
- yalnız dosya küçülsün diye geçmiş kaydı silmek,
- arşiv sırasında kayıt şemasını aynı anda değiştirmek,
- canlı bot ve panel kod değişikliğini aynı arşiv commit'ine karıştırmak.

## 5. Panel stabilite modu

V3.32.9 sonrası varsayılan karar **yeni özellik eklememektir**. Panel işi öncelik sırasıyla:

1. gerçek telefon kullanımında işlemi zorlaştıran sürtünme,
2. gerçek masaüstü kullanımında işlemi zorlaştıran sürtünme,
3. hata / kırık akış / yanlış yönlendirme,
4. gereksiz kalabalık veya aynı işi tekrar eden yüzey,
5. ancak bunlar temizse yeni özellik.

Bir panel değişikliği açılmadan önce şu dört bilgi bulunmalıdır:

- hangi cihaz/yüzeyde sorun var,
- kullanıcı hangi işi yapmaya çalışıyordu,
- mevcut davranış ne,
- beklenen daha basit davranış ne.

Değişiklik mümkün olan en küçük modülde yapılır ve GİRİŞSİZ/FREE/PREMIUM/ADMIN erişim sınırlarını, mobil JS'siz temel akışı ve trading çekirdeğini değiştiremez.

## 6. V3.32.9 mimari zinciri

Sabit giriş noktası `dashboard_app.py`'dir. Aktif runtime `dashboard_share_runtime_app.py` üzerinden çalışır.

Korunan sürüm zinciri:

- V3.32.6 — yüzey ve plan paritesi,
- V3.32.7 — kullanıcı yolculuğu, hesap içi şifre değiştirme ve ödeme geri bildirimi,
- V3.32.8 — hesaba bağlı cihazlar arası İzleme Listesi,
- V3.32.9 — gerçek sinyal/işlem/sonuç kayıtlarından paylaşılabilir işlem kartları ve mobil görünürlük düzeltmesi.

Bu katmanların hiçbiri trading stratejisine yazma yetkisi vermez.

## 7. Bakım döneminin başarı ölçütü

Bakım dönemi başarılı sayılırsa:

- canlı Premium/Scalp/Pump veri akışı kesilmez,
- Swing/Portfolio/Post-Result örnekleri büyür,
- TP3 runner ileri örnekte ölçülmeye devam eder,
- hiçbir ledger 8 MB kırmızı sınırına plansız ulaşmaz,
- panelde yeni özellik sayısından çok hata/sürtünme azalır,
- trading çekirdeğinde gereksiz değişiklik yapılmaz.

Özet politika: **çekirdeği koru, veriyi büyüt, ölçmeden değiştirme, paneli sadeleştir.**
