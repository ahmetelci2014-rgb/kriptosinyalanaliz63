# Sistem Kontrol Merkezi

- Sürüm: `SYSTEM_CONTROL_CENTER_V1_1_2026_08_11`
- Mod: `READ_ONLY_MONITOR_NO_TELEGRAM_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY`
- Üretim: 2026-08-13T23:03:08+00:00
- Genel sağlık: 🟢 **GREEN**

## Sistemler

| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |
|---|---:|---:|---|---:|
| Premium MTF | 🟢 GREEN | 4 | 🟢 KORU | 0.20s |
| Scalp Radar | 🟢 GREEN | 0 | 🟡 İZLE | 0.55s |
| Pump/Dump Radar | 🟢 GREEN | 0 | 🟢 KORU / İZLE | 0.09s |
| Swing Radar | 🟢 GREEN | 3 | 🔴 CANLI İŞLEM KAYNAĞINI DURDUR | 1.02s |
| Ana Trend Pozisyon Radarı | 🟢 GREEN | 4 | ⚪ VERİ TOPLA | 0.95s |
| Tüm Piyasa Keşif Radarı | 🟢 GREEN | 0 | ⚪ VERİ TOPLA | 0.04s |
| Momentum Shadow | 🟢 GREEN | - | 🟠 GÖLGEDE TUT / CANLIYA ALMA | 0.69s |
| Range Cycle Shadow | 🟢 GREEN | - | 🔴 CANLIYA ALMA / YENİDEN TASARLA | 0.61s |
| Portfolio Risk | 🟢 GREEN | - | 🟡 PORTFÖY RİSKİNİ İZLE | 0.62s |
| Decision Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 1.44s |
| Prescription Engine | 🟢 GREEN | - | ⚪ KARAR YOK | 1.44s |
| New Listing Radar | 🟢 GREEN | - | ⚪ KARAR YOK | 0.10s |
| TP Sonrası / Post Result Shadow | 🟢 GREEN | - | 🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET | 0.20s |

## Güvenlik

- `auto_apply = false`
- Telegram göndermez.
- Emir açmaz.
- Mevcut bot state/ledger dosyalarına yazmaz.
- Strateji/config/TP/SL değiştirmez.

- Kritik: Yok
- Dikkat: Yok
