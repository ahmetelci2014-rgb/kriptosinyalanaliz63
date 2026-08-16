# Kripto Kontrol Paneli — V3.32.6 Yüzey / Plan Parite Denetimi

Tarih: 16 Ağustos 2026

Bu belge yeni özellik fikri listesi değildir. Aktif panel zinciri kod üzerinden `GİRİŞSİZ → FREE → PREMIUM → ADMIN` ve `masaüstü → mobil` olarak karşılaştırılmış, yalnız aynı kullanıcının temel işini bir yüzeyde yapamamasına neden olan farklar **parite açığı** sayılmıştır.

## Durum anahtarı

- **VAR:** Özellik o yüzeyde mevcut.
- **SINIRLI:** Bilerek daha az veri gösterir.
- **PREMIUM:** Sunucu tarafında Premium/Admin ile sınırlıdır.
- **ADMIN:** Yalnız yönetici rolüne aittir.
- **MASAÜSTÜ TASARIMI:** Mobil stabilite nedeniyle özellikle masaüstünde bırakılmıştır; parite açığı sayılmaz.
- **YOK / GÜVENLİK:** Altyapı eksikliği nedeniyle güvenli biçimde sunulamaz; rastgele eklenmemelidir.

## Yüzey matrisi

| Kullanıcı işi / özellik | GİRİŞSİZ | FREE masaüstü | FREE mobil | PREMIUM masaüstü | PREMIUM mobil V3.32.6 | ADMIN masaüstü | ADMIN mobil |
|---|---|---|---|---|---|---|---|
| Ürün vitrini / plan karşılaştırma | VAR | — | — | — | — | — | — |
| Giriş / kayıt | VAR | VAR | VAR | VAR | VAR | VAR | VAR |
| Genel sistem özeti | VAR, anonim | VAR | VAR | VAR | VAR | VAR | VAR |
| Temel public piyasa | SINIRLI | VAR | VAR | VAR | VAR | VAR | VAR |
| Coin bazlı canlı sinyal | PREMIUM | kilitli | kilitli | VAR | VAR | VAR | VAR |
| Giriş / TP / SL seviyeleri | PREMIUM | kilitli | kilitli | VAR | VAR | VAR | VAR |
| Sinyaller → İşlemler → Sonuçlar | PREMIUM | kilitli | kilitli | VAR | VAR | VAR | VAR |
| Sinyal / işlem / sonuç arama-filtre | PREMIUM | kilitli | kilitli | VAR | **V3.32.6 ile VAR, sunucu GET** | VAR | VAR |
| Piyasa Merkezi | plan vitrini | VAR | VAR | VAR | VAR | VAR | VAR |
| Coin Merkezi | PREMIUM | kilitli | kilitli | VAR | VAR, sunucu SVG | VAR | VAR |
| İzleme Listesi | Premium vaadi | kilitli | kilitli | VAR | **V3.32.6 ile VAR** | VAR | VAR |
| Fırsat Merkezi | Premium vaadi | kilitli | kilitli | VAR | **V3.32.6 ile VAR** | VAR | VAR |
| 80+ / teknik yön / hacim filtreleri | Premium vaadi | kilitli | kilitli | VAR | **V3.32.6 ile VAR** | VAR | VAR |
| İnceleme Skoru | Premium vaadi | kilitli | kilitli | VAR | **V3.32.6 ile mevcut skor motorunu kullanır** | VAR | VAR |
| Tarayıcı Bildirim Merkezi | PREMIUM | kilitli | kilitli | VAR | mobil JS'siz mimaride yok | VAR | mobil JS'siz mimaride yok |
| Sesli / renkli tarayıcı uyarısı | Premium vaadi | kilitli | kilitli | VAR | **MASAÜSTÜ TASARIMI** | VAR | **MASAÜSTÜ TASARIMI** |
| Hesabım | kayıt sonrası | VAR | VAR | VAR | VAR | VAR | VAR |
| Premium / ödeme durumu | kayıt sonrası | VAR | VAR | VAR | VAR | VAR | VAR |
| 7 / 3 / 1 gün yenileme | — | ilgili değil | ilgili değil | VAR | VAR | VAR | VAR |
| Ödeme geçmişi / bekleyen ödeme engeli | — | VAR | VAR | VAR | VAR | VAR | VAR |
| Performans / geçmiş sonuç | anonim özet | SINIRLI | SINIRLI | VAR | VAR | VAR | VAR |
| Kullanıcı / üyelik / ödeme yönetimi | — | — | — | — | — | ADMIN | Yönetim bağlantısı; derin yönetim masaüstü öncelikli |
| Teknik Görünüm / gelişmiş sistem kalite araçları | — | — | — | gizli | gizli | ADMIN | **MASAÜSTÜ TASARIMI** |
| Deney / öğrenme / gölge karar araçları | — | — | — | gizli | gizli | ADMIN | **MASAÜSTÜ TASARIMI** |
| Kendi kendine “Şifremi unuttum” | YOK / GÜVENLİK | YOK / GÜVENLİK | YOK / GÜVENLİK | YOK / GÜVENLİK | YOK / GÜVENLİK | yönetici sıfırlayabilir | yönetici sıfırlayabilir |

## Kod denetiminde doğrulanan temel sınırlar

1. `/api/dashboard`, `/api/market/opportunities`, `/api/market/analysis-score` ve `/advanced` FREE kullanıcıya sunucu tarafında kapalıdır.
2. Coin Merkezi özet API'si kendi Premium kontrolünü ayrıca yapar.
3. FREE mobil görünüm ham `open_trades` / coin / Giriş / TP / SL listesini render etmez; public özet kullanır.
4. ADMIN teknik ekranları normal Premium üyeye açılmaz.
5. Mobil stabil yapı bilinçli olarak JavaScript'sizdir; daha önce yaşanan dokunma/tıklama kilitlenmesini geri getirecek SPA katmanları V3.33–V3.37 yeniden etkinleştirilmemiştir.
6. V3.32.6 içinde `main.py`, `strategy.py`, `config.py`, Telegram, radar, TP/SL/BE ve state/ledger yazımları değiştirilmez.

## Denetimde bulunan gerçek parite açıkları ve V3.32.6 karşılığı

### 1. Mobil alt navigasyon tutarsızlığı — DÜZELTİLDİ

Önceki durumda Premium ana mobil sayfada `İşlem` sekmesi varken Piyasa/Hesap sayfalarında kayboluyordu. FREE Hesap/Premium sayfasında aynı Premium sayfasına giden tekrarlı sekmeler oluşabiliyordu.

V3.32.6 tek çekirdek navigasyon sözleşmesi kullanır:

- FREE: **Ana · Piyasa · Premium · Hesap**
- PREMIUM/ADMIN: **Ana · Sinyal · İşlem · Sonuç · Hesap**

Piyasa/İzleme/Fırsat araçları ekran içi keşif bağlantılarında tutulur; alt menü kalabalıklaştırılmaz.

### 2. Mobil Sinyal/İşlem/Sonuç filtresi — DÜZELTİLDİ

Masaüstünde coin, yön, sistem ve sonuç filtreleri vardı. V3.32.6 mobilde JavaScript eklemeden GET tabanlı sunucu filtreleri kullanır:

- coin arama,
- LONG / SHORT,
- sistem metni,
- TP / SL / BE.

### 3. Mobil İzleme Listesi — DÜZELTİLDİ

Masaüstünde mevcut ve vitrin tarafından Premium özelliği olarak vaat edilen İzleme Listesi mobilde yoktu. V3.32.6:

- Premium/Admin ile sınırlı,
- en fazla 12 coin,
- public OKX fiyat / 24s hareket,
- mevcut sistem bağlamı,
- isteğe bağlı mevcut İnceleme Skoru teknik özeti,
- yalnız o tarayıcıya ait HttpOnly + SameSite tercih çerezi

kullanır. Sunucu üyelik verisine veya işlem kayıtlarına yazmaz.

### 4. Mobil Fırsat Merkezi / teknik filtreler — DÜZELTİLDİ

Masaüstündeki mevcut Fırsat Merkezi ve İnceleme Skoru motoru mobilde yoktu. V3.32.6 aynı mevcut hesaplamaları JS'siz sunucu görünümünde tekrar kullanır. Filtreler:

- Tümü,
- 80+ İnceleme Skoru,
- teknik yukarı / aşağı,
- aktif sinyal,
- 15m hacim oranı 1.5x+.

Sıralama: grup, skor, 24s hareket, hacim oranı. Bu ekran **yeni sinyal üretmez**.

### 5. Vitrin “sesli uyarı” cihaz kapsamı — DÜZELTİLDİ

Sesli/renkli tarayıcı uyarıları masaüstü Premium zincirinde gerçekten vardır. Mobilin güvenli mimarisi ise JS'sizdir. V3.32.6 vitrin metnini “masaüstünde sesli/renkli uyarı” diye açıklar; mobilde var olmayan bir özelliği varmış gibi sunmaz.

### 6. Mobil ana ekrandaki “Öne çıkan sinyaller” ifadesi — DÜZELTİLDİ

Kod gerçek bir sıralama modeli uygulamadan ilk üç açık kaydı gösteriyordu. Bu nedenle başlık “**Güncel sinyaller**” olarak düzeltilir; olmayan bir sıralama yeteneği ima edilmez.

## Bilinçli olarak eklenmeyenler

- **Mobil ses/Web Audio:** Önceki mobil SPA/touch regresyonları nedeniyle JS'siz temel korunur.
- **Admin deney/öğrenme ekranlarının mobil eşlenmesi:** Üye işi değildir; mobil ürünü kalabalıklaştırır ve operasyonel ekranlar masaüstü önceliklidir.
- **Kendi kendine şifre kurtarma:** Kullanıcı kaydında doğrulanmış e-posta/telefon kurtarma kimliği yoktur. E-posta/telefon doğrulaması kurulmadan “şifremi unuttum” eklemek güvenli değildir. Mevcut yönetici sıfırlama mekanizması korunur.
- **Yeni trading filtresi / yeni sinyal skoru / otomatik emir:** Bu parite paketinin kapsamı değildir.

## Kalıcı regresyon kuralı

`test_dashboard_surface_parity.py` bundan sonra plan ve cihaz paritesinin temel sözleşmesini CI'da korur. Yeni bir ürün sürümü, aynı planın temel kullanıcı işini masaüstünden veya mobilden yanlışlıkla kaldırırsa test güncellenmeden sessizce ana dala geçmemelidir.
