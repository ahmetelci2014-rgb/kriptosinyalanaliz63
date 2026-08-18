# Kripto Sinyal Sistemi — Proje Durumu

Tarih: 4 Ağustos 2026  
Kaynak depo: `ahmetelci2014-rgb/kriptosinyalanaliz63`  
Ana dal: `main`

## Değişmez çalışma kuralları

- Sistem otomatik emir açmaz; işlemler manuel açılır.
- Düşük kaldıraç, Isolated marjin ve zorunlu stop yaklaşımı korunur.
- Kalite sinyal sayısından önemlidir.
- Tekil sonuca göre acele filtre değişikliği yapılmaz.
- Kazanan işlem profilleri korunur.
- Yeni filtreler önce gölge modda ölçülür.
- Canlı dosyalarda büyük ve toplu değişiklik yapılmaz.
- Repo `main` dalı ve JSON ledger/state dosyaları güncel kaynak kabul edilir.

## Aktif sistemler

### Portfolio Risk V3.1

- Aynı coin aynı yönde farklı botlarda çakışırsa engeller.
- Aynı coin ters yönde açıksa engeller.
- Aynı yön risk üst sınırı: `4.0`.
- Toplam açık risk üst sınırı: `8.0`.
- TP1 görmüş açık işlem `0.5` risk ağırlığıyla sayılır.
- Kararlar `portfolio_risk_shadow.json` dosyasına kaydedilir.

İlk gözlenen kayıtlar:
- XTZUSDT SHORT: ALLOW.
- DOODUSDT SHORT: DIRECTION_RISK_LIMIT nedeniyle BLOCK.

### Momentum Shadow v1

- Yalnız MAIN MTF içindeki `5M_RADAR` erken işlem sinyallerini değerlendirir.
- Canlı sinyali engellemez.
- Telegram mesajı göndermez.
- Otomatik emir açmaz.
- Sonuçlar `momentum_shadow.json` dosyasına yazılır.
- Etiketler: `PASS`, `CAUTION`, `WOULD_BLOCK`.
- İlk dosya başarıyla oluştu; başlangıçta uygun açık 5M_RADAR işlemi olmadığı için kayıt sayısı `0`.

### Bot Core Tests

- Portfolio Risk V3 hard-cap kurallarıyla uyumludur.
- 3 açık LONG + yeni aday = 4.0 → geçer, yoğunluk uyarısı verir.
- 4 açık LONG + yeni aday = 5.0 → `DIRECTION_RISK_LIMIT` ile engellenir.
- Son çalışma yeşildir.

## Gözlem dönemi

Şimdilik yeni canlı filtre eklenmeyecek.

Minimum gözlem hedefi:
- Yaklaşık 2–3 günlük temiz veri veya
- En az 10–20 sonuçlanmış erken işlem.

Bu sürede ölçülecekler:
- Portfolio Risk kararlarının doğru ve yanlış engellemeleri.
- Momentum Shadow kararlarının TP/SL/BE/Net R sonuçlarıyla ilişkisi.
- Kazanan profillerin yanlışlıkla WOULD_BLOCK alıp almadığı.

## Sıradaki geliştirme sırası

1. TP1 sonrası BE yönetimi gölge testi.
2. Dinamik stop gölge testi.
3. İlk 15 dakika işlem durumu motoru.
4. Bakiye ve stop yüzdesine göre pozisyon büyüklüğü önerisi.
5. Scalp, Pump/Dump ve Swing için ayrı momentum gölge doğrulaması.
6. Yeterli kanıt oluşursa seçili filtrelerin canlıya kontrollü alınması.

## Korunacak başarılı profiller

- MOVEUSDT TP3
- STXUSDT TP2
- MASKUSDT TP3
- RENDERUSDT TP2
- Eski referans profiller: XPLUS ve BICO

## Değişiklik politikası

- Kullanıcı açık onayı olmadan repo davranışını değiştiren canlı düzenleme yapılmaz.
- Önce ölçüm, sonra karşılaştırma, sonra küçük ve geri alınabilir değişiklik yapılır.
- `strategy.py`, `main.py` ve `config.py` yalnız yeterli veri ve açık onay sonrası değiştirilir.
