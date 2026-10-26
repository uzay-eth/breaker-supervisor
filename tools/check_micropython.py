"""Compile all shipped Python modules with the actual MicroPython compiler.

This is a syntax/bytecode check, not RP2350 peripheral/runtime execution.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    compiler = shutil.which("mpy-cross")
    if not compiler:
        raise SystemExit("From supervisor root: uv run --offline --group verify python tools/check_micropython.py")
    output = ROOT / "scratch" / "mpy"
    output.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT/"breaker").glob("*.py")) + sorted((ROOT/"firmware").glob("*.py"))
    subprocess.run([compiler, "--version"], check=True)
    for source in sources:
        target = output / (source.parent.name + "-" + source.stem + ".mpy")
        subprocess.run([compiler, "-o", str(target), str(source)], check=True)
    print("PASS: %d firmware/shared modules compile; hardware runtime untested" % len(sources))


if __name__ == "__main__": main()
