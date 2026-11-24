"""Deprecated compatibility entry point.

The previous implementation concatenated datasets with incompatible target and
measurement semantics. Use phase1_preprocess.py and phase2_train.py instead.
Metro Interstate data is intentionally kept as a separate domain benchmark.
"""
import sys


def main() -> int:
    print("ERROR: Cross-domain merge/retrain is disabled because Metro Interstate and Bhubaneswar measurements are not comparable.")
    print("Run: python phase1_preprocess.py && python phase2_train.py")
    print("For the current model, Bhubaneswar synthetic data is the primary training domain.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
