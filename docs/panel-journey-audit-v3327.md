# Kripto Kontrol Paneli — V3.32.7 Uçtan Uca Kullanıcı Akışı Denetimi

Tarih: 16 Ağustos 2026

Bu belge yeni özellik fikir listesi değildir. V3.32.6 yüzey/plan paritesi korunarak gerçek kullanıcı yolculuğu kod üzerinden adım adım kontrol edilmiştir:

`GİRİŞSİZ → KAYIT → FREE → PREMIUM BAŞVURUSU → ÖDEME ONAYI → PREMIUM → YENİLEME → HESAP → ADMIN`

Yalnız var olan akışın kullanıcı tarafından tamamlanmasını engelleyen veya sonucu belirsiz bırakan noktalar açık sayılmıştır.

## Akış matrisi

| Adım | Mevcut durum | Sonuç |
|---|---|---|
| Girişsiz vitrin | Ürün/plan karşılaştırma, kayıt ve giriş bağlantıları var | TAM |
| FREE kayıt | CSRF, IP kayıt limiti, kullanıcı adı doğrulama, 10+ karakter şifre ve tekrar doğrulama var | TAM |
| Kayıt sonrası | Hesap oluşturulur, oturum açılır ve kullanıcı panele yönlenir | TAM |
| FREE kullanım | Public piyasa/özet açık; Premium veri sunucu tarafında kapalı | TAM |
| Premium ödeme bildirimi | Banka/FAST/Havale; opsiyonel kripto; bekleyen ikinci bildirim engeli | TAM |
| Ödeme hatası geri bildirimi | Backend hatasında kullanıcı yalnız Premium sayfasına dönüyordu | **V3.32.7 İLE TAMAMLANDI** |
| Admin ödeme kararı | Onay/ret; onay Premium süre başlatır veya mevcut sürenin üzerine ekler | TAM |
| Premium kullanım | Sinyal/işlem/sonuç, Coin, İzleme, Fırsat ve teknik araçlar | TAM |
| Yenileme | 7/3/1 gün uyarısı; bekleyen ödeme çift yenilemeyi engeller; kalan gün yanmaz | TAM |
| Süre bitimi | Hesap kapanmaz; FREE plana düşer ve tekrar Premium başlatabilir | TAM |
| Hesap/ödeme geçmişi | Plan, bitiş, son ödeme ve geçmiş ödeme durumu görünür | TAM |
| Kullanıcının kendi şifresini değiştirmesi | Yalnız admin reset vardı; kullanıcı kendi hesabında yapamıyordu | **V3.32.7 İLE TAMAMLANDI** |
| Şifremi unuttum | Doğrulanmış e-posta/telefon kurtarma kimliği yok | **YOK / GÜVENLİK NEDENİYLE EKLENMEDİ** |
| Admin kullanıcı yönetimi | Aktif/pasif, rol, süre, şifre reset, plan ve yaşam döngüsü | TAM |

## V3.32.7 ile kapatılan gerçek açıklar

### 1. Oturum içinden şifre değiştirme

Önceki durumda normal FREE/Premium kullanıcı kendi şifresini değiştiremiyor; yalnız yönetici yeni şifre atayabiliyordu. Bu, hesabına erişimi olan kullanıcı için eksik bir temel güvenlik akışıydı.

V3.32.7:

- `/account/security` server-rendered ve JavaScript gerektirmeyen güvenlik ekranı ekler,
- mevcut şifreyi doğrulamadan değişiklik yapmaz,
- yeni şifreyi mevcut 10+ karakter politika ile doğrular,
- yeni şifre tekrarını kontrol eder,
- mevcut şifre ile aynı yeni şifreyi reddeder,
- yalnız `panel_users` deposunda yönetilen hesaplarda çalışır,
- başarılı değişiklikten sonra o kullanıcıya ait bütün açık panel oturumlarını iptal eder,
- kurucu/ortam hesabının şifresini panelden değiştirmez; bu hesap sunucu ortam ayarından yönetilmeye devam eder.

### 2. Ödeme bildirimi kullanıcı geri bildirimi

Önceki `POST /payment/notify` akışında başarısız kayıt kullanıcıyı sessizce `/premium` sayfasına döndürebiliyordu. Backend davranışı doğru olsa bile kullanıcı neden işlem olmadığını anlayamıyordu.

V3.32.7 ödeme/üyelik store kurallarını değiştirmeden sabit geri bildirim kodları ekler:

- bildirim başarıyla kaydedildi,
- zaten onay bekleyen bildirim var,
- oturum/CSRF doğrulaması yenilenmeli,
- kripto ödeme kapalı,
- kullanıcı deposu geçici olarak erişilemiyor,
- geçersiz ödeme bildirimi.

Ham GitHub/backend hata metni kullanıcıya gösterilmez.

## Bilinçli olarak değiştirilmediler

- FREE/PREMIUM/ADMIN erişim sınırları,
- Premium API koruması,
- ödeme tahsilatı (sistem hâlâ otomatik para çekmez),
- admin ödeme onayı/ret mantığı,
- 7/3/1 gün yenileme ve mevcut süre üzerine ekleme,
- mobil JS'siz temel mimari,
- masaüstü V3.32.1 runtime onarımı,
- `main.py`, `strategy.py`, `config.py`, radarlar, Telegram, TP/SL/BE, state/ledger.

## Şifre kurtarma neden hâlâ yok?

`Şifremi unuttum` ile `Şifremi değiştir` aynı şey değildir. Oturum içindeki kullanıcı mevcut şifresini kanıtlayabildiği için V3.32.7 güvenli biçimde şifre değiştirebilir. Ancak oturum dışı kurtarma için doğrulanmış e-posta veya telefon gibi ikinci bir kimlik kanıtı gerekir. Mevcut kullanıcı modelinde bu alanlar ve doğrulama akışı yoktur; bu nedenle tahmine dayalı bir kurtarma mekanizması eklenmemiştir.

## Kalıcı regresyon sözleşmesi

`test_dashboard_account_flow.py` ve mevcut panel testleri bundan sonra şunları korur:

- mevcut şifre olmadan şifre değişmez,
- yanlış mevcut şifre reddedilir,
- yeni şifre başarıyla kaydolunca eski şifre geçersiz olur,
- hesap güvenliği hem mobil hem masaüstü hesap yüzeyinden keşfedilebilir,
- ödeme geri bildirimi yalnız sabit güvenli kodlardan üretilir,
- V3.32.6 paritesi yeni hesap katmanının altında korunur,
- trading çekirdeği bu katmanda yer almaz.
