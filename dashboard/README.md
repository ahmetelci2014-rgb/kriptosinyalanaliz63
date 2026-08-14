# Şifreli Canlı Kripto Kontrol Paneli

Panel, private GitHub reposundaki gerçek JSON state ve ledger dosyalarını sunucu tarafında okur. Tarayıcı açıkken varsayılan olarak her 30 saniyede yenilenir. V1.7; V1.6 yönetici/üye veri ayrımının üstüne çoklu kullanıcı, rol, üyelik süresi ve şifre yönetimi ekler.

## Güvenlik sınırları

- Otomatik emir açmaz.
- Kullanıcı parası veya kripto varlığı tutmaz.
- Borsa hesabı yönetmez.
- Telegram mesajı göndermez.
- Sinyal stratejisi, `main.py`, `strategy.py` ve `config.py` davranışını değiştirmez.
- Kesin kazanç veya kâr garantisi vermez.
- GitHub erişim anahtarları tarayıcıya ve HTML kaynağına gönderilmez.
- Kullanıcı şifreleri düz metin olarak saklanmaz; PBKDF2-SHA256 özeti tutulur.
- OKX canlı grafiği yalnız herkese açık piyasa verisini okur; borsa API anahtarı istemez ve emir endpoint'i içermez.

## Canlı çalışma şekli

1. Kullanıcı HTTPS adresinden giriş yapar.
2. Tarayıcı panel verisi için yalnız `/api/dashboard`, piyasa grafiği için `/api/market/candles` adresine bağlanır.
3. Python sunucusu private GitHub reposundaki sinyal verilerini salt-okunur token ile kontrol eder.
4. Yönetici hesabı kullanıcı yönetim ekranına girebilir.
5. Dinamik kullanıcı hesapları `panel-users` veri dalındaki `panel_users.json` dosyasında tutulur.
6. Kullanıcı hesabı değişiklikleri için ayrı bir GitHub token kullanılır; sinyal verisi tokeni read-only kalır.
7. GitHub veri kaynağı geçici olarak erişilemezse panel mümkün olduğunda son geçerli sinyal verisini gösterir.

## Panelde görünenler

- Premium/Main, Scalp, Pump/Dump ve Yeni Liste açık gerçek işlemleri
- TP, SL, break-even ve süresi dolan kayıtlar
- Her sistemin örnek sayısı, TP/SL oranı ve kesin R toplamı
- Kapanış sırasına göre kümülatif Net R ve maksimum düşüş
- LONG ve SHORT işlemler için ayrı örnek, TP/SL oranı, toplam ve ortalama Net R
- Türkiye tarihine göre son 30 günlük Net R grafiği ve gün analizi
- Son 7/30 gün ile önceki eşit dönemin sistem bazlı karşılaştırması
- JSON kaynakları için güncellik/eskilik durumu
- İstenen USDT coininde 1m, 5m, 15m, 1H, 4H ve 1D canlı mumlar
- Açık işlem grafiğinde Giriş, TP1, TP2, TP3 ve SL çizgileri
- Kapanmış işlemlerde işlem tarihinin çevresindeki geçmiş mumlar ve çıkış seviyesi
- 7, 30, 90 gün veya tüm kayıtlar için performans görünümü
- Coin, sistem, sonuç ve dönem bazlı işlem geçmişi filtreleri
- İşlem geçmişinde sayfalama
- Açık risk özeti ve sistem bazlı açık işlem dağılımı
- Yönetici için filtreli CSV dışa aktarma
- System Control teknik sağlık durumu

## Yönetici ve üye veri ayrımı

| Bölüm | Yönetici | Üye |
| --- | --- | --- |
| Aktif sinyaller, coin grafiği ve sonuçlar | Görür | Görür |
| Genel performans ve yön/gün grafiği | Görür | Görür |
| Canlı sistem kararları ve açık risk özeti | Görür | Göremez |
| Kaynak dosyaları ve veri güncelliği teşhisi | Görür | Göremez |
| Dönem yönetim karşılaştırması ve System Control ayrıntıları | Görür | Göremez |
| Filtreli CSV dışa aktarma | Görür | Göremez |
| Kullanıcı yönetim ekranı | Görür | Göremez |

Yetki ayrımı yalnız arayüzde yapılmaz. MEMBER oturumunda `/api/dashboard` cevabı sunucu tarafında filtrelenir; gizlenen yönetim verisi tarayıcıya gönderilmez.

## V1.7 çoklu kullanıcı yönetimi

Yönetici panel üst çubuğundaki **Kullanıcılar** bağlantısından `/admin/users` ekranına gider.

Yapılabilen işlemler:

- Sınırsız kullanım için süre alanını boş bırakarak üye oluşturma
- 1-3650 gün arası üyelik süresi verme
- MEMBER veya ADMIN rolü verme
- Kullanıcıyı aktif/pasif yapma
- Üyelik süresini yenileme veya süresiz yapma
- Şifre sıfırlama
- Rol değiştirme
- Pasifleştirme, rol değiştirme veya şifre sıfırlamada kullanıcının mevcut panel oturumlarını kapatma

Kurucu yönetici `PANEL_USERNAME` + `PANEL_PASSWORD_HASH` hesabıdır. Bu hesap ortam değişkeninde kalır ve acil erişim hesabı olarak kullanıcı yönetim ekranından kapatılamaz.

Dinamik kullanıcı verisi `panel-users` dalında tutulur. Bu dalın amacı üyelik verisi değiştiğinde `main` dalında gereksiz deployment tetiklenmesini önlemektir. `panel_users.json` yalnız kullanıcı adı, rol, aktiflik, süre ve PBKDF2 şifre özeti içerir; düz şifre içermez.

## Gerekli ortam değişkenleri

### Sinyal verisi için

- `GITHUB_PANEL_TOKEN`: yalnız private repo için **Contents: Read-only** fine-grained token
- `GITHUB_REPOSITORY=ahmetelci2014-rgb/kriptosinyalanaliz63`
- `GITHUB_REF_NAME=main`

### Kurucu yönetici için

- `PANEL_USERNAME=ahmet`
- `PANEL_PASSWORD_HASH`: `dashboard_live_app.py --hash-password` ile üretilen yönetici şifre özeti

### Çoklu kullanıcı yönetimi için

- `GITHUB_PANEL_USERS_TOKEN`: yalnız bu private repo için **Contents: Read and write** yetkili ayrı fine-grained token
- `PANEL_USERS_REF=panel-users`
- `PANEL_USERS_PATH=panel_users.json`

### Diğer

- `PANEL_REFRESH_SECONDS=30`
- `PANEL_SESSION_HOURS=12`
- `PANEL_COOKIE_SECURE=1`
- `PANEL_TRUST_PROXY=1`

`PANEL_MEMBER_USERNAME` ve `PANEL_MEMBER_PASSWORD_HASH` V1.6 geriye uyumluluğu için hâlâ desteklenir; V1.7 kullanılırken yeni üyeler kullanıcı yönetim ekranından açılmalıdır.

Token ve şifre hiçbir zaman README, YAML veya Actions loglarına düz değer olarak yazılmaz. `render.yaml` gizli değerleri yalnız `sync: false` olarak tanımlar.

## Güvenli şifre özeti üretme

```bash
python dashboard_live_app.py --hash-password
```

Bu komut kurucu yönetici şifresi için kullanılır. Dinamik kullanıcıların şifre özetini V1.7 kullanıcı yönetimi otomatik üretir.

## Yerel deneme

Repo JSON dosyalarının bulunduğu bilgisayarda:

```powershell
$env:PANEL_USERNAME="ahmet"
$env:PANEL_PASSWORD="yalnız-kendinizin-bildiği-şifre"
$env:PANEL_COOKIE_SECURE="0"
python dashboard_accounts_app.py --root .
```

`GITHUB_PANEL_USERS_TOKEN` tanımlı değilse panel kurucu yöneticiyle çalışır ve kullanıcı yönetim ekranı hesap deposunu salt-okunur/kapalı gösterir.

## İnternette 24 saat çalışma

`render.yaml` ve `Dockerfile.dashboard` V1.7 giriş noktası olarak `dashboard_accounts_app.py` dosyasını kullanır. `/healthz` endpoint'i V1.7 sürümünü bildirir.

## Testler

`Kripto Panel Kontrolü` workflow'u şu kontrolleri çalıştırır:

- `dashboard_builder.py`
- `dashboard_live_app.py`
- `dashboard_accounts_app.py`
- panel testleri
- canlı panel testleri
- çoklu kullanıcı yönetimi testleri
- gerçek verilerden statik panel üretimi

Statik HTML artifact yalnız acil durum anlık görüntüsüdür; canlı panel değildir.
