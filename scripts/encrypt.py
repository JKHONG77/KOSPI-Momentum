#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
분석 결과(data/out/data.json)를 비밀번호로 암호화 -> docs/data.enc
- PBKDF2-HMAC-SHA256(30만 회)로 키 생성, AES-256-GCM 암호화
- 비밀번호: 환경변수 DASHBOARD_PASSWORD (GitHub Secrets)
- 브라우저(대시보드)가 같은 방식으로 복호화합니다.
"""
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "out" / "data.json"
DST = ROOT / "docs" / "data.enc"
ITER = 300_000


def main():
    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    if not pw:
        sys.exit("DASHBOARD_PASSWORD 가 없습니다. Settings > Secrets and variables > Actions 에 등록하세요.")
    if not SRC.exists():
        print("분석 결과가 없어 암호화를 건너뜁니다.")
        return
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, SRC.read_bytes(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps({"v": 1, "iter": ITER, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}))
    print(f"암호화 저장: {DST} ({DST.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
