from pathlib import Path
import joblib

files = sorted(Path("models").rglob("*.pkl"))
files += sorted(Path("models").rglob("*.joblib"))

print("\nMODEL COMPATIBILITY CHECK")
print("=" * 60)

for file in files:
    try:
        obj = joblib.load(file)
        print(f"OK     : {file} -> {type(obj).__name__}")
    except Exception as error:
        print(f"FAILED : {file}")
        print(f"ERROR  : {type(error).__name__}: {error}")

print("=" * 60)
