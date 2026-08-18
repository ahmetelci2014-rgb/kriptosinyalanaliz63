# Şifreli Canlı Kripto Kontrol Paneli

Bu README aktif panel mimarisini anlatır. V3.32.9 sonrası bakım/stabilite politikası için `docs/system-maintenance-v3329.md`; sürüm doğrulamaları için `docs/panel-journey-audit-v3327.md`, `docs/panel-watchlist-sync-v3328.md` ve `docs/panel-share-cards-v3329.md` dosyalarına bakın.

## Aktif sürüm ve giriş noktası

- Aktif sürüm: `KRIPTO_KONTROL_MERKEZI_V3_32_9_SHARE_CARDS_MOBILE_FIX_2026_08_17`
- Sabit uygulama giriş noktası: `dashboard_app.py`
- Aktif runtime modülü: `dashboard_share_runtime_app.py`
- Docker komutu: `python dashboard_app.py --host 0.0.0.0`
- Sağlık endpoint'i: `/healthz`

`dashboard_app.py` yalnız sabit giriş noktasıdır; aktif `VERSION`, `main` ve handler zincirini `dashboard_share_runtime_app.py` üzerinden alır. Böylece deploy ayarı değişmeden V3.32 ürün zinciri tek giriş noktasında korunur.

## Korunan sürüm zinciri

Panel V3.32 tabanını korur ve aşağıdaki katmanları birlikte çalıştırır:

- **V3.32.1:** masaüstü Premium/Admin klasik görünümünün runtime onarımı.
- **V3.32.3:** mobil ana ekranın JS'siz sunucu görünümü.
- **V3.32.4:** mobil Piyasa/Coin yüzeyleri.
- **V3.32.5:** mobil Hesap/Premium yüzeyleri.
- **V3.32.6:** GİRİŞSİZ/FREE/PREMIUM/ADMIN yüzey ve plan paritesi.
- **V3.32.7:** uçtan uca kullanıcı yolculuğu; oturum içinden şifre değiştirme ve güvenli ödeme geri bildirimi.
- **V3.32.8:** yönetilen Premium/Admin hesaplarda cihazlar arası senkron İzleme Listesi.
- **V3.32.9:** gerçek sinyal/işlem/sonuç kayıtlarından paylaşılabilir işlem kartları ve mobil Paylaş görünürlük düzeltmesi.

V3.32.7–V3.32.9 yeni trading stratejisi değildir. `main.py`, `strategy.py`, `config.py`, radarlar, Telegram, TP/SL/BE ve trading state/ledger yazımları bu ürün katmanlarının dışında tutulur.

## V3.32.9 sonrası stabilite modu

Panel artık özellik biriktirme döneminden **stabilite ve sadelik dönemine** geçmiştir. Varsayılan karar yeni özellik eklememektir.

Öncelik sırası:

1. gerçek telefon kullanımında işi zorlaştıran sürtünmeler,
2. gerçek masaüstü kullanımında işi zorlaştıran sürtünmeler,
3. hata, kırık akış veya belirsiz geri bildirim,
4. gereksiz kalabalık veya aynı işi tekrar eden yüzey,
5. ancak bunlar temizse yeni özellik.

Yeni bir panel değişikliği için cihaz/yüzey, kullanıcının yapmak istediği iş, mevcut sorun ve beklenen daha basit davranış açıkça tanımlanmalıdır. Değişiklik mümkün olan en küçük modülde yapılır.

## Temel ürün modeli

### GİRİŞSİZ

- Ürün vitrini ve plan karşılaştırması
- Anonim genel sistem özeti
- Kayıt ve giriş
- Premium özelliklerin açıklaması
- Coin bazlı giriş/TP/SL veya özel işlem verisi yok

### FREE

- Genel sistem özeti
- Temel public piyasa keşfi
- Hesap ve Premium/ödeme merkezi
- Coin bazlı canlı sinyal, Giriş/TP/SL, İşlem Takibi, Coin Merkezi, İzleme Listesi ve Fırsat Merkezi sunucu tarafında kapalı

### PREMIUM

- Canlı sinyal ayrıntıları
- Sinyaller → İşlemler → Sonuçlar
- Giriş / TP1 / TP2 / TP3 / SL
- Masaüstü ve mobil arama/filtre
- Piyasa Merkezi
- Coin Merkezi
- Hesaba bağlı İzleme Listesi
- Fırsat Merkezi + İnceleme Skoru / 80+ / teknik yön / hacim filtreleri
- Sonuç ve performans geçmişi
- Hesap, ödeme ve 7/3/1 gün yenileme akışı
- Oturum içinden şifre değiştirme
- Gerçek işlem kayıtlarından paylaşılabilir sinyal/işlem/sonuç kartları
- Masaüstünde tarayıcı Bildirim Merkezi ve isteğe bağlı sesli/renkli uyarı

Mobil ürün özellikle JavaScript'siz sunucu görünümünde tutulur. Önceki mobil SPA/touch regresyonlarını geri getirmemek için Web Audio / tarayıcı bildirim merkezi mobilde zorla etkinleştirilmez. Paylaşımda desteklenen cihazlarda Web Share API kullanılabilir; destek yoksa PNG indirme fallback'i korunur.

### ADMIN

Premium ürün görünümüne ek olarak:

- Kullanıcı ve üyelik yönetimi
- Ödeme onay/red akışı
- Yenileme yönetimi
- Teknik Görünüm / sistem kalite araçları
- Deney, öğrenme ve gölge karar araçları

Derin operasyon/teknik ekranlar masaüstü önceliklidir; normal Premium üyeye açılmaz.

## V3.32.6 yüzey/parite tabanı

V3.32.6 yeni bir trading özelliği eklemeden mevcut masaüstü ürününün kullanıcı işlerini JS'siz mobil yüzeyde tamamladı:

- FREE alt menü: **Ana · Piyasa · Premium · Hesap**
- PREMIUM/ADMIN alt menü: **Ana · Sinyal · İşlem · Sonuç · Hesap**
- Sinyal/işlem: coin, LONG/SHORT ve sistem filtresi
- Sonuç: coin, TP/SL/BE ve sistem filtresi
- Vitrinde sesli/renkli uyarının **masaüstü** özelliği olduğunun belirtilmesi
- Mobil ana ekranda sıralama yapmayan “Öne çıkan sinyaller” ifadesinin “Güncel sinyaller” olarak düzeltilmesi

V3.32.7–V3.32.9 bu parite sözleşmesinin üzerinde çalışır.

## Hesap ve kullanıcı yolculuğu — V3.32.7

- FREE kayıt sonrası oturum açılır ve kullanıcı panele yönlenir.
- Premium ödeme bildirimi sabit ve güvenli durum kodlarıyla geri bildirim verir.
- Yönetilen kullanıcı kendi hesabında mevcut şifresini doğrulayarak yeni şifre belirleyebilir.
- Başarılı şifre değişiminde o kullanıcıya ait açık oturumlar iptal edilir.
- Kurucu/env hesabı panel içinden değiştirilmez.
- Doğrulanmış e-posta/telefon kurtarma kimliği olmadığı için güvenli self-service **Şifremi unuttum** akışı hâlâ yoktur.

## İzleme Listesi — V3.32.8

Yönetilen `panel_users` Premium/Admin hesabında İzleme Listesi artık hesap tercihidir ve cihazlar arasında senkronize edilir.

Örnek veri modeli:

```json
{
  "preferences": {
    "watchlist": ["BTCUSDT", "ETHUSDT"],
    "watchlist_updated_at": 0
  }
}
```

Kurallar:

- İlk senkronizasyonda hesapta liste yoksa mevcut cihaz favorileri hesaba taşınabilir.
- Sonrasında hesap listesi tek kaynak olur.
- Maksimum 12 normalize USDT sembolü saklanır.
- Kullanıcı adı istek parametresinden değil oturumdan çözülür.
- FREE ve GİRİŞSİZ kullanıcı için Premium İzleme Listesi API'si kapalıdır.
- `panel_users` içinde olmayan kurucu/env hesabı için masaüstü `localStorage`, mobil cookie fallback'i korunur.

İzleme Listesi trading kararı değildir; trade ledger'a yazılmaz.

## Paylaşılabilir işlem kartları — V3.32.9

PREMIUM/ADMIN kullanıcı gerçek panel kayıtlarından paylaşım kartı oluşturabilir:

- Sinyaller → **Paylaş** → Yeni İşlem Sinyali kartı
- İşlemler → **Paylaş** → Açık İşlem Takibi kartı
- Sonuçlar → **Paylaş** → TP / SL / BE sonuç kartı

Mimari:

- `dashboard_sharecard_app.py` — gerçek kayıt seçimi, SVG/PNG kart üretimi
- `dashboard_shareui_app.py` — mevcut mobil/masaüstü kartlara Paylaş bağlantısı ekleyen sunum katmanı
- `dashboard_share_runtime_app.py` — V3.32.8 zinciri üzerinde erişim kontrolü ve paylaşım rotaları

Paylaşım seçicisi gerçek veriyi istemci parametresinden kabul etmez; Giriş/TP/SL değerleri sunucudaki kayıttan yeniden okunur. Kullanıcı adı karta yazılmaz. Kart üretimi state/ledger dosyalarına yazmaz.

## Güvenlik sınırları

- Otomatik emir açmaz.
- Kullanıcı parası veya kripto varlığı tutmaz.
- Borsa hesabı yönetmez.
- Panel kodu Telegram mesajı göndermez; mevcut bot Telegram akışı ayrı çekirdekte korunur.
- Panel geliştirmesi `main.py`, `strategy.py`, `config.py`, radar mantığı, TP/SL/BE veya trading ledger/state yazımlarını değiştirmez.
- Kesin kazanç veya kâr garantisi vermez.
- GitHub erişim anahtarları tarayıcıya veya HTML kaynağına gönderilmez.
- Kullanıcı şifreleri PBKDF2-SHA256 özeti olarak tutulur.
- OKX grafik/piyasa verisi public endpoint'lerden salt-okunur alınır.
- FREE/PREMIUM sınırı yalnız arayüzde değildir; Premium API'leri sunucu tarafında korunur.
- Paylaşım endpoint'leri FREE kullanıcıya kapalıdır.

Premium korumalı temel endpoint'ler arasında `/api/dashboard`, `/api/market/opportunities`, `/api/market/analysis-score`, `/api/coin-center/summary`, İzleme Listesi hesap API'leri, paylaşım rotaları ve rolüne göre `/advanced` bulunur.

## Üyelik ve ödeme

Mevcut üyelik/ödeme backend'i korunur:

- FREE / PREMIUM / ADMIN planları
- Premium bitiş zamanı
- 7 / 3 / 1 gün kala yenileme görünümü
- `/payment/notify` ödeme bildirimi
- bekleyen ödeme varken ikinci bildirim engeli
- yönetici onay/red akışı
- ödeme geçmişi

Panel ödeme tahsil etmez; ödeme bildirimi yönetici kontrolüne gider.

## Veri kaynakları

Panel private GitHub reposundaki gerçek JSON state/ledger verilerini sunucu tarafında okur. Temel kaynaklar:

- `open_signals.json`
- `scalp_radar_state.json`
- `pump_radar_state.json`
- `new_listing_performance_ledger.json`
- `trade_ledger.json`
- `scalp_performance_ledger.json`
- `pump_performance_ledger.json`
- `system_control_center_report.json`

Piyasa, grafik ve teknik inceleme için public OKX verisi kullanılır. GitHub veri kaynağı geçici erişilemezse mevcut panel katmanları mümkün olduğunda son geçerli veriyi kullanır.

Trading ledger'ları panel tarafından değiştirilmez. Büyüyen ledger'ların arşivleme politikası `docs/system-maintenance-v3329.md` içinde ayrı ve geri alınabilir bakım işlemi olarak tanımlanmıştır.

## Gerekli ortam değişkenleri

### Sinyal verisi

- `GITHUB_PANEL_TOKEN`: private repo için Contents Read-only
- `GITHUB_REPOSITORY=ahmetelci2014-rgb/kriptosinyalanaliz63`
- `GITHUB_REF_NAME=main`

### Kurucu yönetici

- `PANEL_USERNAME`
- `PANEL_PASSWORD_HASH`

### Dinamik kullanıcı yönetimi

- `GITHUB_PANEL_USERS_TOKEN`: kullanıcı veri dalı için Read and write
- `PANEL_USERS_REF=panel-users`
- `PANEL_USERS_PATH=panel_users.json`

### Diğer

- `PANEL_REFRESH_SECONDS=30`
- `PANEL_SESSION_HOURS=12`
- `PANEL_COOKIE_SECURE=1`
- `PANEL_TRUST_PROXY=1`
- `PANEL_CRYPTO_PAYMENT_ENABLED=0/1`

`PANEL_MEMBER_USERNAME` ve `PANEL_MEMBER_PASSWORD_HASH` eski sabit üye uyumluluğu için desteklenmeye devam eder.

Token ve şifre değerleri README/YAML/Actions loglarına düz metin yazılmaz. `render.yaml` gizli değerleri `sync: false` ile tanımlar.

## Güvenli şifre özeti üretme

```bash
python dashboard_live_app.py --hash-password
```

## Yerel çalıştırma

Repo JSON dosyalarının bulunduğu ortamda gerekli panel şifre ve veri kaynağı değişkenlerini tanımladıktan sonra:

```bash
python dashboard_app.py --root . --host 127.0.0.1 --port 8080
```

Production Docker aynı `dashboard_app.py` giriş noktasını kullanır.

## Testler

`Kripto Panel Kontrolü` workflow'u:

- bütün `dashboard_*.py` ve `test_dashboard_*.py` dosyalarını derler,
- bütün panel regresyon testlerini çalıştırır,
- Docker imajını kurar,
- gerçek repo verilerinden panel üretir,
- acil durum statik artifact üretir.

Kalıcı regresyon testleri V3.32.6 yüzey paritesini, V3.32.7 hesap akışını, V3.32.8 İzleme Listesi senkronunu ve V3.32.9 paylaşım katmanını korur.

`Bot Core Tests` panel değişikliklerinin canlı bot Python testlerini ve Yeni Liste Radar self-testini bozmadığını ayrı olarak doğrular.

Statik HTML artifact yalnız acil durum anlık görüntüsüdür; canlı panel değildir.

## Değişiklik politikası

V3.32.9 sonrası panel değişikliği için temel kural:

**Önce ölçülebilir kullanım sürtünmesini azalt; yeni özellik eklemeyi varsayılan çözüm sayma.**

Trading çekirdeği, canlı sinyal akışları ve gölge veri toplama panel bakımından bağımsız korunur.
