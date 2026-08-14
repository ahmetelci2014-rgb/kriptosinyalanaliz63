# Kripto Kontrol Paneli

Panel, repodaki gerçek JSON state ve ledger dosyalarından tek bir çevrimdışı HTML üretir.

## GitHub Actions ile açma

1. **Actions → Kripto Kontrol Paneli → Run workflow** seçilir.
2. Yeşil tamamlanan run açılır.
3. Sayfanın altındaki **Artifacts → kripto-kontrol-paneli** indirilir.
4. ZIP içindeki `index.html` tarayıcıda açılır.

Workflow ayrıca her saat güncel bir özel artifact üretir. Artifact 14 gün saklanır ve repo erişimi olmayan kişilere açık değildir.

## Yerelde üretme

```bash
python dashboard_builder.py --root . --output dashboard_output/index.html
```

Panel salt okunurdur. Dış ağa bağlanmaz, API anahtarı istemez, Telegram göndermez, sinyal veya emir üretmez ve strateji dosyalarını değiştirmez.
