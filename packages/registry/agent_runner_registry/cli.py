"""Registry CLI: list, show, materialize."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loader import load_bundles
from .resolver import resolve
from .materializer import materialize


def _cmd_list(args: argparse.Namespace) -> int:
    bundles = load_bundles(Path(args.sources))
    if not bundles:
        print(f"No bundles found under {args.sources}")
        return 0
    for ref, bundle in sorted(bundles.items(), key=lambda kv: (kv[0].name, kv[0].version)):
        desc = bundle.manifest.get("description", "")
        print(f"{ref}\t{desc}")
    return 0


def _cmd_materialize(args: argparse.Namespace) -> int:
    bundles = load_bundles(Path(args.sources))
    refs = [r.strip() for r in args.agents.split(",") if r.strip()]
    resolved = resolve(refs, bundles)
    manifest = materialize(resolved, Path(args.into), clean=not args.no_clean)
    print(f"Materialized {len(manifest.agents)} agent(s) into {manifest.target_dir}")
    for ref in manifest.agents:
        print(f"  {ref}  sha256:{manifest.content_hashes[str(ref)][:12]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-runner-registry")
    parser.add_argument("--sources", default="agent-sources", help="Directory of agent bundles")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub_list = sub.add_parser("list", help="List available agent bundles")
    sub_list.set_defaults(func=_cmd_list)

    sub_mat = sub.add_parser("materialize", help="Materialize bundles into a target dir")
    sub_mat.add_argument("--into", required=True, help="Target .claude/agents/ directory")
    sub_mat.add_argument("--agents", required=True, help="Comma-separated name@version refs")
    sub_mat.add_argument("--no-clean", action="store_true", help="Do not clear target dir first")
    sub_mat.set_defaults(func=_cmd_materialize)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
