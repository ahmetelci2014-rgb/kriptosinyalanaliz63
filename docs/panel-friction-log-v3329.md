# Panel Gerçek Kullanım Sürtünme Günlüğü — V3.32.9

Başlangıç: 18 Ağustos 2026

Bu günlük yeni özellik listesi değildir. Amaç birkaç günlük gerçek telefon ve masaüstü kullanımında kullanıcı işini yavaşlatan, belirsizleştiren veya gereksiz tıklama yaratan noktaları kaydetmek ve yalnız doğrulanmış sürtünmeleri küçük değişikliklerle çözmektir.

## Stabilite kuralı

Bir kayıt aşağıdaki bilgiler olmadan geliştirme işine dönüşmez:

- cihaz/yüzey,
- yapılmak istenen iş,
- mevcut davranış,
- beklenen daha basit davranış,
- tekrar üretim adımı,
- önem seviyesi.

Trading çekirdeği, sinyal mantığı, TP/SL/BE, Telegram ve trading ledger/state yazımları bu günlük kapsamının dışındadır.

## Öncelik seviyeleri

- **P0 — Kırık:** Kullanıcı temel işi tamamlayamıyor, güvenlik veya veri görünümü hatalı.
- **P1 — Ciddi sürtünme:** İş tamamlanıyor ama yanlış yönlendirme, gereksiz tekrar veya belirgin mobil/masaüstü engel var.
- **P2 — Sadelik:** İş çalışıyor fakat metin, yerleşim veya adım sayısı gereksiz karmaşa yaratıyor.
- **P3 — Fikir:** Yeni özellik isteği. Stabilite döneminde varsayılan olarak bekler.

## Açık kayıtlar

| Tarih | Cihaz / yüzey | Yapılmak istenen iş | Mevcut sürtünme | Beklenen davranış | Seviye | Durum |
|---|---|---|---|---|---|---|
| — | — | — | Doğrulanmış gerçek kullanım sürtünmesi henüz kaydedilmedi | — | — | İzleniyor |

## Bir düzeltmenin kabul kapısı

Düzeltme ancak şu koşullarla tamamlanmış sayılır:

1. Tekrar üretim adımı artık sorunu göstermiyor.
2. Aynı kullanıcı işi mobil ve/veya masaüstünde beklenen kadar kısa ve anlaşılır.
3. GİRİŞSİZ/FREE/PREMIUM/ADMIN erişim sınırı bozulmadı.
4. Mevcut panel regresyon testleri yeşil.
5. Trading çekirdeği dosyalarında değişiklik yok.
6. Yeni özellik eklemek yerine mümkünse mevcut yüzey sadeleştirildi.

## Kapanmış kayıt formatı

Bir sorun çözüldüğünde aşağıdaki bilgiler saklanır:

- sorun özeti,
- etkilenen cihaz/yüzey,
- kök neden,
- yapılan en küçük değişiklik,
- doğrulayan test veya manuel akış,
- ilgili commit,
- varsa geri alma notu.

## Ürün kararı

V3.32.9 sonrası panel için başarı ölçüsü özellik sayısı değildir. Başarı; daha az sürtünme, daha az belirsizlik, daha az gereksiz yüzey ve daha kararlı gerçek kullanımdır.
