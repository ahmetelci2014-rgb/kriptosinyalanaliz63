# V3.32.9 — Paylaşılabilir İşlem Kartları

## Amaç
Kripto Kontrol Merkezi içindeki gerçek PREMIUM/ADMIN sinyal, açık işlem ve sonuç kayıtlarını sosyal medyada paylaşılabilir 1080×1350 karta dönüştürmek.

## Akış
- Sinyaller: `Paylaş` → Yeni İşlem Sinyali kartı.
- İşlemler: `Paylaş` → Açık İşlem Takibi kartı.
- Sonuçlar: `Paylaş` → TP / SL / BE sonuç kartı.
- Kart, panel kaydındaki Giriş / TP1 / TP2 / TP3 / SL değerlerini ve public 15M mumlarını kullanır.
- Mobilde Web Share API destekleniyorsa PNG dosyası doğrudan cihaz paylaş menüsüne gönderilir; destek yoksa PNG indirilir.
- SVG görseli ayrıca salt-okunur olarak açılabilir.

## Güvenlik ve ürün sınırı
- FREE kullanıcı paylaşım endpoint'lerine erişemez.
- Paylaşım URL'si yalnız işlem seçicisini taşır; Giriş/TP/SL görsel değerleri sunucudaki gerçek kayıttan yeniden okunur.
- Kullanıcı adı sosyal kartta gösterilmez.
- Kart oluşturma hiçbir işlem/state/ledger dosyasına yazmaz.
- Strategy, config, radarlar, Telegram, TP/SL/BE davranışı, ödeme ve üyelik kuralları değişmez.
- Grafik yalnız public piyasa mumlarını salt-okunur kullanır.

## Mimari
- `dashboard_sharecard_app.py`: kayıt seçimi, SVG kart ve PNG/Web Share önizlemesi.
- `dashboard_shareui_app.py`: mevcut mobil/masaüstü kartlara Paylaş bağlantısı ekleyen sunum dekoratörü.
- `dashboard_share_runtime_app.py`: V3.32.8 runtime üzerinde erişim kontrolü ve `/share/trade`, `/share/card.svg` rotaları.
