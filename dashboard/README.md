# Şifreli Canlı Kripto Kontrol Paneli

Bu README aktif panel mimarisini anlatır. Ayrıntılı masaüstü / mobil / GİRİŞSİZ / FREE / PREMIUM / ADMIN karşılaştırması için `docs/panel-surface-audit-v3326.md` dosyasına bakın.

## Aktif sürüm ve giriş noktası

- Aktif sürüm: `KRIPTO_KONTROL_MERKEZI_V3_32_6_SURFACE_PARITY_2026_08_16`
- Sabit uygulama giriş noktası: `dashboard_app.py`
- Aktif modül: `dashboard_runtimefix_app.py`
- Docker komutu: `python dashboard_app.py --host 0.0.0.0`
- Sağlık endpoint'i: `/healthz`

Panel V3.32 ürün tabanını korur. Masaüstü Premium/Admin klasik görünümü V3.32.1 runtime onarımıyla çalışır. Mobil ana ekran V3.32.3, mobil Piyasa/Coin V3.32.4, mobil Hesap/Premium V3.32.5 sunucu görünümlerini korur. V3.32.6 yalnız denetimde doğrulanan yüzey/parite açıklarını kapatır.

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
- İzleme Listesi
- Fırsat Merkezi + İnceleme Skoru / 80+ / teknik yön / hacim filtreleri
- Sonuç ve performans geçmişi
- Hesap, ödeme ve 7/3/1 gün yenileme akışı
- Masaüstünde tarayıcı Bildirim Merkezi ve isteğe bağlı sesli/renkli uyarı

Mobil ürün özellikle JavaScript'siz sunucu görünümünde tutulur. Önceki mobil SPA/touch regresyonlarını geri getirmemek için Web Audio / tarayıcı bildirim merkezi mobilde zorla etkinleştirilmez.

### ADMIN

Premium ürün görünümüne ek olarak:

- Kullanıcı ve üyelik yönetimi
- Ödeme onay/red akışı
- Yenileme yönetimi
- Teknik Görünüm / sistem kalite araçları
- Deney, öğrenme ve gölge karar araçları

Derin operasyon/teknik ekranlar masaüstü önceliklidir; normal Premium üyeye açılmaz.

## V3.32.6 mobil parite düzeltmeleri

V3.32.6 yeni bir trading özelliği eklemez. Mevcut masaüstü ürününün kullanıcı işlerini JS'siz mobil yüzeyde tamamlar:

- FREE alt menü: **Ana · Piyasa · Premium · Hesap**
- PREMIUM/ADMIN alt menü: **Ana · Sinyal · İşlem · Sonuç · Hesap**
- Sinyal/işlem: coin, LONG/SHORT ve sistem filtresi
- Sonuç: coin, TP/SL/BE ve sistem filtresi
- Premium mobil İzleme Listesi: en fazla 12 coin, tarayıcı tercih çerezi
- Premium mobil Fırsat Merkezi: mevcut masaüstü fırsat grupları ve mevcut İnceleme Skoru motoru
- Vitrinde sesli/renkli uyarının **masaüstü** özelliği olduğu açıkça belirtilir
- Mobil ana ekrandaki sıralama yapmayan “Öne çıkan sinyaller” ifadesi “Güncel sinyaller” olarak düzeltilir

## Güvenlik sınırları

- Otomatik emir açmaz.
- Kullanıcı parası veya kripto varlığı tutmaz.
- Borsa hesabı yönetmez.
- Panel kodu Telegram mesajı göndermez; mevcut bot Telegram akışı ayrı çekirdekte korunur.
- Panel geliştirmesi `main.py`, `strategy.py`, `config.py`, radar mantığı, TP/SL/BE veya ledger/state yazımlarını değiştirmez.
- Kesin kazanç veya kâr garantisi vermez.
- GitHub erişim anahtarları tarayıcıya veya HTML kaynağına gönderilmez.
- Kullanıcı şifreleri PBKDF2-SHA256 özeti olarak tutulur.
- OKX grafik/piyasa verisi public endpoint'lerden salt-okunur alınır.
- FREE/PREMIUM sınırı yalnız arayüzde değildir; Premium API'leri sunucu tarafında korunur.

Premium korumalı temel endpoint'ler arasında `/api/dashboard`, `/api/market/opportunities`, `/api/market/analysis-score`, `/api/coin-center/summary` ve rolüne göre `/advanced` bulunur.

## Mobil veri ve tercih modeli

Mobil sayfalar JavaScript olmadan normal URL/GET bağlantılarıyla çalışır. Sinyal/işlem/sonuç filtreleri sunucu tarafında uygulanır.

İzleme Listesi kullanıcı hesabının veya trade ledger'ın içine yazılmaz. En fazla 12 sembol yalnız o tarayıcı için `HttpOnly`, `SameSite=Lax` tercih çerezinde saklanır. Bu tercih bir trading kararı değildir ve başka cihazlara senkronize edilmez.

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

## Hesap ve şifre notu

Dinamik hesaplarda kullanıcı adı, rol, aktiflik, üyelik süresi ve PBKDF2 şifre özeti bulunur. Yönetici kullanıcı şifresini sıfırlayabilir ve oturumlarını sonlandırabilir.

Kullanıcı kaydında doğrulanmış e-posta/telefon kurtarma kimliği bulunmadığından güvenli self-service **“Şifremi unuttum”** akışı şu anda yoktur. E-posta/telefon doğrulaması kurulmadan böyle bir akış eklenmemelidir.

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

`test_dashboard_surface_parity.py`, GİRİŞSİZ/FREE/PREMIUM/ADMIN ile masaüstü/mobil temel ürün sözleşmesini ayrıca korur.

`Bot Core Tests` panel değişikliklerinin canlı bot Python testlerini ve Yeni Liste Radar self-testini bozmadığını ayrı olarak doğrular.

Statik HTML artifact yalnız acil durum anlık görüntüsüdür; canlı panel değildir.
