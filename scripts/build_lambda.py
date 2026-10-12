"""Assemble the Lambda package in build/lambda without Docker.

Linux wheels for the Lambda runtime (python3.13, x86_64, Amazon Linux 2023) are
fetched with pip's --platform flag, then the application, the built web app
and the evaluation artifact are copied in.

    python scripts/build_lambda.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "lambda"
PLATFORMS = ["manylinux_2_28_x86_64", "manylinux_2_27_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--target", str(OUT),
           "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:", "--upgrade",
           "-r", str(ROOT / "services" / "api" / "requirements.txt")]
    for p in PLATFORMS:
        cmd += ["--platform", p]
    subprocess.run(cmd, check=True)

    shutil.copytree(ROOT / "services" / "api" / "proofplate", OUT / "proofplate",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"))
    dist = ROOT / "apps" / "web" / "dist"
    if (dist / "index.html").exists():
        shutil.copytree(dist, OUT / "proofplate" / "static")
    ev = ROOT / "evaluation" / "outputs" / "evaluation.json"
    if ev.exists():
        shutil.copy(ev, OUT / "proofplate" / "assets" / "evaluation.json")

    # trim what the runtime never loads
    for pattern in ("**/__pycache__", "numpy/tests", "numpy/*/tests", "numpy/_core/tests", "numpy/f2py", "numpy/_pyinstaller",
                    "numpy/doc", "bin"):
        for p in OUT.glob(pattern):
            shutil.rmtree(p, ignore_errors=True)
    for pattern in ("**/*.pyi", "**/*.pyc"):
        for p in OUT.glob(pattern):
            p.unlink()

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    native = sorted({f.name for f in OUT.rglob("*.so")})[:4]
    print(f"lambda package: {size / 1e6:.1f} MB unzipped, native modules e.g. {native}")
    if size > 240e6:
        raise SystemExit("package is over Lambda's 250 MB unzipped limit")
    if not any(OUT.rglob("_multiarray_umath*.so")):
        raise SystemExit("no Linux NumPy binary found: the wheel platform did not match")


if __name__ == "__main__":
    main()
