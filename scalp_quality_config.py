"""Scalp canlı kalite ayarlarının tek kaynak dosyası.

Bu modül yalnız ATAK_SCALP kapanış gücü guardını ve onun eski 62/38
karşılaştırma gölgesini tanımlar. TEPKI_SCALP, TP/SL/BE veya Telegram
kurallarını değiştirmez.
"""

VERSION = "SCALP_ATAK_QUALITY_GUARD_V1_2026_08_18"

# Canlı ATAK_SCALP kapanış gücü guardı — tek canlı kaynak.
LIVE_ATTACK_LONG_MIN_CLOSE_POWER = 70.0
LIVE_ATTACK_SHORT_MAX_CLOSE_POWER = 30.0

# Sadece karşılaştırma gölgesinde kullanılan eski eşikler.
LEGACY_ATTACK_LONG_MIN_CLOSE_POWER = 62.0
LEGACY_ATTACK_SHORT_MAX_CLOSE_POWER = 38.0

SHADOW_FILE = "scalp_attack_guard_shadow.json"
SHADOW_VERSION = "SCALP_ATTACK_GUARD_SHADOW_V1_2026_08_18"
SHADOW_MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_LIVE_SIGNAL_CHANGE"
SHADOW_KEEP_DAYS = 14
SHADOW_MAX_RECORDS = 300
SHADOW_MAX_TRACK_MINUTES = 180
SHADOW_DUPLICATE_SECONDS = 2 * 60 * 60
SHADOW_MIN_RESOLVED_SAMPLE = 30
