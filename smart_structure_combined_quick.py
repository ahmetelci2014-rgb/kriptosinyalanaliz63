"""Quick gate before expensive combined validation."""
import smart_structure_combined_fast as validation

validation.SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
validation.LOOKBACK_DAYS = 2

if __name__ == "__main__":
    validation.run()
