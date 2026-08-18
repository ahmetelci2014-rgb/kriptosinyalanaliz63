# OKX Demo Pilot

Bu pilot yalnız OKX **Demo Trading / simulated trading** ortamı içindir. Gerçek hesap emri göndermez ve mevcut Premium/Scalp/Pump sinyal üretimini değiştirmez.

## Amaç

İlk aşamada yalnız execution zincirini doğrular:

1. `open_signals.json` içinden en yeni uygun Premium sinyali seçer.
2. Sinyal en az 91 skor olmalıdır.
3. TP1 görülmüş sinyal yeniden açılmaz.
4. Sinyal 30 dakikadan eskiyse açılmaz.
5. Güncel OKX fiyatı sinyal entry'sinden %0.25'ten fazla uzaksa açılmaz.
6. OKX USDT perpetual (`*-USDT-SWAP`) kontrat bilgisine göre miktar hesaplanır.
7. 5 USDT demo margin ve 2x isolated kullanılır.
8. Demo hesabında zaten açık pozisyon varsa ikinci pozisyon açılmaz.
9. Market emir ile birlikte sinyalin SL ve TP3 seviyeleri attached koruma olarak gönderilir.
10. Emir dolduktan sonra attached TP/SL doğrulanamazsa demo pozisyon güvenlik için kapatılır.

Bu sürüm **TP1/TP2 kısmi kâr yönetimini otomatikleştirmez**. İlk amaç emir açma, miktar, yön, isolated leverage ve borsa tarafı koruma zincirini güvenli biçimde doğrulamaktır.

## OKX Demo API anahtarı

OKX üzerinde gerçek API anahtarı kullanılmamalıdır. Demo Trading ekranında ayrı Demo Trading API Key oluşturulmalıdır.

GitHub repository secrets:

- `OKX_DEMO_API_KEY`
- `OKX_DEMO_SECRET_KEY`
- `OKX_DEMO_PASSPHRASE`

Repository variable:

- `OKX_DEMO_ENABLED`

İlk kurulumda `OKX_DEMO_ENABLED=false` bırakılabilir. `plan` modu API secret kullanmadan yalnız public OKX verileriyle emir planı üretir. Gerçek **demo** emir için değişken `true` yapılır ve workflow `execute` modunda manuel çalıştırılır.

## Workflow

`.github/workflows/okx-demo-pilot.yml`

Yalnız `workflow_dispatch` vardır; otomatik schedule yoktur.

### İlk test

1. GitHub > Actions > **OKX Demo Pilot**
2. `Run workflow`
3. `mode = plan`
4. `trade_id` boş bırakılabilir

Bu aşama OKX'e emir göndermez.

### Demo emir testi

Demo API secrets eklendikten ve `OKX_DEMO_ENABLED=true` yapıldıktan sonra:

1. GitHub > Actions > **OKX Demo Pilot**
2. `mode = execute`
3. Uygun yeni Premium sinyal varsa tek demo pozisyon açılır

## Güvenlik sınırları

- `x-simulated-trading: 1` tüm private isteklerde zorunludur.
- Workflow schedule içermez.
- Gerçek OKX API anahtarı kullanılmaz.
- Aynı anda yalnız 1 demo pozisyon.
- 2x isolated.
- Demo margin 5 USDT.
- 91 altı skor yok.
- TP1 görmüş sinyal yok.
- 30 dakikadan eski sinyal yok.
- Entry drift > %0.25 ise emir yok.
- Minimum kontrat boyutu hedef notionalin %50'den fazla üzerine zorluyorsa emir yok.
- Attached TP/SL doğrulanamazsa demo pozisyon kapatılır.

## Canlıya geçiş yok

Bu pilot başarılı olsa bile gerçek para otomasyonu otomatik açılmaz. Önce demo emir zinciri ve hata senaryoları doğrulanır; canlı pilot ayrı karar ve ayrı güvenlik paketi gerektirir.
