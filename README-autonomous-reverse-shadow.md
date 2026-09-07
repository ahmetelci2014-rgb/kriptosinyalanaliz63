# Autonomous Trade & Reverse Shadow V1

Bu modül gerçek emir açmadan, OKX USDT perpetual piyasasında otonom giriş + pozisyon yönetimi + reverse kararını sanal olarak test eder.

## Neden ayrı modül?

Mevcut `main.py`, `strategy.py` ve canlı sinyal akışına dokunmaz. Yeni fikir önce ölçülür; performans kanıtlanmadan gerçek emir katmanı eklenmez.

## Karar akışı

1. OKX aktif ve likit USDT perpetual pariteleri taranır.
2. Mevcut `strategy.analyze_mtf_trade` kullanılarak 4H / 1H / 15M MTF girişleri aranır.
3. Minimum giriş skoru varsayılan `80`.
4. Sanal pozisyon açılır ve 5M mumlarla takip edilir.
5. Pozisyon en az `+0.55R` kârdayken Reverse Engine devreye girebilir.
6. Reverse için hem 5M hem 15M yapının ters yöne dönmesi zorunludur.
7. Reverse Score varsayılan `90/100` altında ise ters pozisyon açılmaz.
8. Reverse pozisyon boyutu skora göre küçültülür:
   - 90-92: %25
   - 93-95: %40
   - 96-98: %60
   - 99-100: %75
9. Aynı chain içinde en fazla 1 reverse vardır.
10. Reverse anında ayrıca `NO_REVERSE` benchmark başlatılır. Böylece sistem, “reverse yaptık” sonucu ile “eski yönde devam etseydik” sonucunu ayrı ayrı ölçer.

## Ölçülen ana sonuç

Ledger zincir bazında şunları hesaplar:

- `strategy_weighted_net_r`
- `benchmark_net_r`
- `reverse_contribution_r`

`reverse_contribution_r > 0` ise reverse kararı benchmark'a göre değer katmıştır.

Tahmini komisyon + slippage maliyeti R hesabından düşülür. Varsayılan model her yön için taker fee %0.05 + slippage %0.02 kullanır. Gerçek OKX hesap tier'ı ve emir tipi farklı olabilir.

## Güvenlik

- OKX API key kullanmaz.
- Emir göndermez.
- Telegram mesajı göndermez.
- Gerçek bakiye veya pozisyonlara erişmez.
- Ayrı ledger/state dosyaları kullanır.
- İlk workflow sadece `workflow_dispatch` ile manueldir.

## Dosyalar

- `autonomous_reverse_shadow.py`
- `test_autonomous_reverse_shadow.py`
- `.github/workflows/autonomous-reverse-shadow.yml`
- Çalışınca oluşur:
  - `autonomous_reverse_shadow_state.json`
  - `autonomous_reverse_shadow_ledger.json`

## Canlıya geçiş kriteri

Bu V1'in amacı “kâr garanti etmek” değildir. Reverse katkısı, net R, drawdown ve yeterli örnek sayısı ile ölçülür. Gerçek emir entegrasyonu ancak shadow sonuçları anlamlı ve tekrar edilebilir bir avantaj gösterirse düşünülmelidir.
