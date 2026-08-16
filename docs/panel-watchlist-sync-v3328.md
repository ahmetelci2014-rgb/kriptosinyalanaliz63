# Kripto Kontrol Paneli — V3.32.8 Hesaba Bağlı İzleme Listesi

Tarih: 16 Ağustos 2026

## Neden yapıldı?

Mevcut üründe İzleme Listesi iki farklı cihaz-local depoda tutuluyordu:

- masaüstü: `localStorage` (`kripto_focus_favs`),
- mobil: HttpOnly tercih cookie'si (`kripto_watch_v3326`).

Bu yüzden aynı Premium kullanıcı telefonda ve bilgisayarda farklı favori coin listeleri görebiliyordu. V3.32.8 yeni bir sinyal özelliği değil; mevcut İzleme Listesini gerçek kullanıcı hesabına bağlayan ürün geliştirmesidir.

## Yeni davranış

Yönetilen `panel_users` Premium/Admin hesabında:

1. İlk senkronizasyonda hesapta daha önce liste yoksa mevcut cihaz favorileri hesaba taşınır.
2. Bu ilk geçişten sonra hesap listesi tek kaynak olur.
3. Masaüstünde ekleme/kaldırma hesabı günceller.
4. Mobilde ekleme/kaldırma aynı hesap listesini günceller.
5. Diğer cihaz açıldığında hesap listesi cihaza uygulanır.
6. Bir cihazda kaldırılan coin, başka cihazdaki eski local kayıt yüzünden tekrar eklenmez.
7. Maksimum 12 coin ve mevcut sembol doğrulama sınırı korunur.

## Kurucu / ortam hesabı

Ortam değişkeniyle yönetilen ve `panel_users` içinde olmayan kurucu hesap için güvenli fallback korunur:

- masaüstü localStorage,
- mobil cookie.

Bu hesap için kullanıcı deposuna sahte kayıt açılmaz.

## Veri modeli

Yalnız yönetilen kullanıcının mevcut kaydına şu tercih alanı eklenebilir:

```json
{
  "preferences": {
    "watchlist": ["BTCUSDT", "ETHUSDT"],
    "watchlist_updated_at": 0
  }
}
```

Bu alan:

- planı değiştirmez,
- Premium bitiş tarihini değiştirmez,
- ödeme kaydını değiştirmez,
- kullanıcı şifresini değiştirmez,
- admin yaşam döngüsü metriklerini değiştirmez.

## Erişim sınırı

- GİRİŞSİZ: erişim yok.
- FREE: hesap İzleme Listesi API'si ve Premium İzleme Listesi kapalı.
- PREMIUM: cihazlar arası senkron açık.
- ADMIN: `panel_users` tarafından yönetilen admin hesapta senkron açık; kurucu/env hesapta fallback.

## Güvenlik

- GET API yalnız oturumlu Premium/Admin kullanıcıya kendi listesini verir.
- Yazma işlemi aynı-origin POST ve mevcut session CSRF ile korunur.
- Kullanıcı adı istekten alınmaz; oturumdan çözülür.
- Başka kullanıcının favorilerine erişim parametresi yoktur.
- En fazla 12 normalize USDT sembolü saklanır.
- Ham GitHub tokeni veya kullanıcı dosyası tarayıcıya çıkmaz.

## Değişmeyen sistemler

- `main.py`
- `strategy.py`
- `config.py`
- Premium/Scalp/Pump/New Listing sinyal üretimi
- Telegram
- TP/SL/BE
- state/ledger dosyaları
- ödeme ve Premium yenileme kuralları
- V3.32.7 şifre değiştirme/ödeme geri bildirimi
- mobil JS'siz temel sayfalar

Masaüstünde yalnız mevcut İzleme Listesi sayfasına küçük senkron köprüsü eklenir. Mobil İzleme Listesi sunucu-rendered kalır.
