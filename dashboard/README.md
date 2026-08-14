# Şifreli Canlı Kripto Kontrol Paneli

Panel, private GitHub reposundaki gerçek JSON state ve ledger dosyalarını
sunucu tarafında okur. Tarayıcı açıkken her 30 saniyede bir yenilenir.

## Güvenlik sınırları

- Otomatik emir açmaz.
- Kullanıcı parası veya kripto varlığı tutmaz.
- Borsa hesabı yönetmez.
- Telegram mesajı göndermez.
- Sinyal stratejisi ve config dosyalarını değiştirmez.
- Kesin kazanç veya kâr garantisi vermez.
- GitHub erişim anahtarı tarayıcıya ve HTML kaynağına gönderilmez.

## Canlı çalışma şekli

1. Kullanıcı şifreli HTTPS adresinden giriş yapar.
2. Tarayıcı yalnız \`/api/dashboard\` adresine bağlanır.
3. Python sunucusu private GitHub reposunu salt-okunur token ile kontrol eder.
4. Değişmeyen dosyalarda ETag önbelleği kullanılır.
5. GitHub geçici olarak erişilemezse son geçerli veri gösterilir ve uyarı çıkar.

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
