#!/usr/bin/env python
"""Generate and write a fresh V2 data-bridge keypair.

Invoked by ``pixi run gen-data-bridge-keypair``. Writes to
``~/.finextract/data-bridge.{pub,priv}``. Refuses to overwrite
existing files; delete them manually first if you really want to
rotate (the keypair has no rotation story yet).
"""

from __future__ import annotations

import sys

from engine.data_bridge_crypto import generate_keypair
from engine.data_bridge_keys import PRIVKEY_PATH, PUBKEY_PATH, write_keypair


def main() -> int:
    pub, priv = generate_keypair()
    try:
        write_keypair(pub, priv)
    except FileExistsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Delete the existing files manually if you intend to rotate.", file=sys.stderr)
        return 1
    print(f"Wrote {PUBKEY_PATH} (0644)")
    print(f"Wrote {PRIVKEY_PATH} (0600)")
    print()
    print("Copy data-bridge.pub to any host that should be able to encrypt exports.")
    print("Keep data-bridge.priv on this host (and paste into the public site browser).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
