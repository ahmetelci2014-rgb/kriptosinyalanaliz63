# Şifreli Canlı Kripto Kontrol Paneli

Panel, private GitHub reposundaki gerçek JSON state ve ledger dosyalarını
sunucu tarafında okur. Tarayıcı açıkken her 30 saniyede bir yenilenir. V1.3;
sistem bazlı performans analitiği, veri kaynağı güncellik kontrolü ve canlı
coin/işlem grafiği içerir.

## Güvenlik sınırları

- Otomatik emir açmaz.
- Kullanıcı parası veya kripto varlığı tutmaz.
- Borsa hesabı yönetmez.
- Telegram mesajı göndermez.
- Sinyal stratejisi ve config dosyalarını değiştirmez.
- Kesin kazanç veya kâr garantisi vermez.
- GitHub erişim anahtarı tarayıcıya ve HTML kaynağına gönderilmez.
- OKX canlı grafiği yalnız herkese açık piyasa verisini okur; borsa API
  anahtarı istemez ve emir endpoint'i içermez.

## Canlı çalışma şekli

1. Kullanıcı şifreli HTTPS adresinden giriş yapar.
2. Tarayıcı yalnız \`/api/dashboard\` adresine bağlanır.
3. Python sunucusu private GitHub reposunu salt-okunur token ile kontrol eder.
4. Değişmeyen dosyalarda ETag önbelleği kullanılır.
5. GitHub geçici olarak erişilemezse son geçerli veri gösterilir ve uyarı çıkar.

## Panelde görünenler

- Premium, Scalp, Pump/Dump ve Yeni Liste açık gerçek işlemleri
- TP, SL, break-even ve süresi dolan kayıtlar
- Her sistemin örnek sayısı, TP/SL oranı ve kesin R toplamı
- Kapanış sırasına göre kümülatif Net R ve maksimum düşüş
- Sekiz JSON kaynağı için güncellik/eskilik durumu
- İstenen USDT coininde 1m, 5m, 15m, 1H, 4H ve 1D canlı mumlar
- Coin sistemde açık işlemse grafikte Giriş, TP1, TP2, TP3 ve SL çizgileri
- Kapanmış TP/SL işlemine tıklayınca işlem tarihinin çevresindeki geçmiş mumlar
- Kapanmış işlem grafiğinde Giriş, TP1–TP3, SL ve Çıkış seviyeleri
- 7, 30, 90 gün veya tüm kayıtlar için ayrı performans görünümü
- Coin, sistem, sonuç ve dönem bazlı işlem geçmişi filtreleri
- Uzun işlem geçmişinde 20/50 kayıt seçenekli önceki/sonraki sayfalama
- Açık işlemlerde LONG/SHORT dengesi, ortalama/en geniş stop mesafesi ve
  ortalama TP1/TP3 hedef-risk oranı
- Sistem bazlı açık işlem dağılımı ve eksik/geniş stop bilgi notu
- Seçili coin, sistem, sonuç ve dönem filtrelerini koruyan CSV dışa aktarma
- System Control teknik sağlık durumu

State dosyalarının uzun süre değişmemesi tek başına workflow arızası sayılmaz;
teknik kritik sağlık kararı System Control raporundan gelir. Scalp state içindeki
`last_sent`, `early_last_sent` ve `prewatch_last_sent` zamanları veri güncelliği
hesabına katılır.

Canlı mumlar sunucu tarafındaki `/api/market/candles` adresinden gelir. Bu
adres de panel oturumu gerektirir, sembol ve periyot doğrular ve aynı isteği
kısa süre önbelleğe alır. Önce OKX perpetual swap, yoksa spot USDT paritesi
denenir. Arayüzde herhangi bir coin sembolü yazılabilir; açık veya kapanmış
işlem satırındaki coin adına tıklanınca ilgili grafik otomatik açılır. Kapanmış
işlemlerde OKX `history-candles` verisi işlem zamanının çevresinden okunur.

CSV raporu yalnız tarayıcıda, o anda uygulanan filtrelerden üretilir. Sunucuya
yeni veri yazmaz ve indirilen dosyada Excel formül enjeksiyonuna karşı hücre
başlangıçları güvenli hale getirilir.

## Yerel deneme

Repo JSON dosyalarının bulunduğu bilgisayarda PowerShell:

\`\`\`powershell
$env:PANEL_USERNAME="ahmet"
$env:PANEL_PASSWORD="yalnız-kendinizin-bildiği-şifre"
$env:PANEL_COOKIE_SECURE="0"
python dashboard_live_app.py --root .
\`\`\`

Ardından \`http://127.0.0.1:8080\` açılır. Yerel dosyalar değiştikçe panel
yenilenir.

## Güvenli şifre özeti üretme

\`\`\`bash
python dashboard_live_app.py --hash-password
\`\`\`

Komutun ürettiği değer barındırma hizmetinde \`PANEL_PASSWORD_HASH\` gizli
değişkenine yazılır. Şifre veya hash GitHub dosyalarına eklenmez.

## İnternette 24 saat çalışma

Repo kökündeki \`render.yaml\` ve \`Dockerfile.dashboard\`, HTTPS sunan bir
barındırma hizmetinde dağıtım için hazırlanmıştır.

Gizli ortam değişkenleri:

- \`GITHUB_PANEL_TOKEN\`: yalnız bu private repo için **Contents: Read-only**
  yetkili fine-grained GitHub token
- \`PANEL_PASSWORD_HASH\`: yukarıdaki komutla üretilen şifre özeti

Normal ortam değişkenleri:

- \`GITHUB_REPOSITORY=ahmetelci2014-rgb/kriptosinyalanaliz63\`
- \`GITHUB_REF_NAME=main\`
- \`PANEL_USERNAME=ahmet\`
- \`PANEL_REFRESH_SECONDS=30\`
- \`PANEL_COOKIE_SECURE=1\`
- \`PANEL_TRUST_PROXY=1\`

Token ve şifre hiçbir zaman sohbette, README'de, YAML içinde veya Actions
loglarında paylaşılmaz.

## Statik yedek

\`Kripto Panel Kontrolü\` workflow'u manuel çalıştırıldığında üç gün saklanan
bir statik HTML yedeği üretir. Bu dosya yalnız acil durum görüntüsüdür ve
canlı panel yerine kullanılmaz.
