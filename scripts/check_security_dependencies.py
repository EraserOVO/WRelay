"""Reject stale bundled dependencies before packaging an installer."""
from importlib.metadata import version


def main():
    expected = {"cryptography": "50.0.1", "nonebot-adapter-qq": "1.7.2+wrelay.1", "multidict": "6.9.1"}
    for package, required in expected.items():
        actual = version(package)
        if actual != required:
            raise SystemExit(f"{package}: expected {required}, found {actual}. Run scripts/prepare_runtime.ps1 first.")
    print("Security dependency versions verified")


if __name__ == "__main__":
    main()
