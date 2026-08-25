# Smart Entry Historical Bootstrap

Bu araştırma katmanı Premium V4 canlı kararlarını değiştirmez.

Amaç: Smart Entry giriş bölgelerinin sıfırdan yalnız canlı shadow verisi beklemesi yerine, geçmiş OKX 15M mumlarından başlangıç kanıtı üretmek.

## Yöntem
- Kronolojik breakout/impulse olayları yalnız o anda mevcut mumlarla tespit edilir.
- Adaptive retracement aday bölgeleri test edilir: 0.30–0.42, 0.42–0.52, 0.52–0.62, 0.62–0.72, 0.72–0.79.
- S/R Flip retest ayrıca ölçülür.
- 1R/2R/3R, SL-first, MFE ve MAE kaydedilir.
- Veri zaman sırasıyla %70 eğitim / %30 holdout olarak ayrılır.
- Eğitimde iyi görünen bölge, görülmemiş holdout bölümünde de yeterli değilse `HISTORICALLY_VALIDATED` olmaz.

## Canlı kullanım
Tarihsel sonuç tek başına canlı filtre değildir. Çıktı `BOOTSTRAP_ONLY_NOT_A_LIVE_GATE` olarak işaretlenir.

Tarihsel olarak doğrulanan bölge daha sonra kısa süreli canlı shadow doğrulamasına aday olur. OKX order-book/taker-flow geçmişi bu araştırmada yeniden oluşturulmadığı için Movement Start V3 order-flow tarafı ileriye dönük canlı kanıt gerektirir.

## Çalıştırma
`Smart Entry Historical Bootstrap` workflow'u yalnız manuel çalışır. Otomatik 5 dakikalık Premium workflow'a eklenmemiştir; Action kotası tüketmez.
