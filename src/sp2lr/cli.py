"""sp2lr command line.

  sp2lr login                        # cache Microsoft 365 tokens
  sp2lr extract                      # SharePoint -> out/extract/*.json
  sp2lr plan [--mapping mapping.yaml]  # -> out/plan.json + out/plan.md
  sp2lr load [--phase P ...] [--dry-run]
  sp2lr verify                       # -> out/verify.md
"""

from __future__ import annotations

import argparse
import json
import logging

from .config import liferay_settings, out_dir, sharepoint_settings


def main(argv: list[str] | None = None) -> None:
    from .load import PHASES

    ap = argparse.ArgumentParser(prog="sp2lr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="sign in to Microsoft 365 and cache tokens (needed by the MCP server)")

    ex = sub.add_parser("extract", help="extract the SharePoint site")
    ex.add_argument("--no-attachments", action="store_true", help="skip downloading list item attachments")
    ex.add_argument("--no-libraries", action="store_true", help="skip document libraries")
    ex.add_argument("--max-file-mb", type=int, default=250, help="skip library files larger than this (default 250)")

    pl = sub.add_parser("plan", help="build the migration plan")
    pl.add_argument("--mapping", default="mapping.yaml")

    ld = sub.add_parser("load", help="load the plan into Liferay")
    ld.add_argument("--phase", action="append", choices=PHASES, help="run only these phases (repeatable)")
    ld.add_argument("--dry-run", action="store_true", help="record writes to out/dry-run.jsonl instead of sending them")

    sub.add_parser("verify", help="reconcile counts and list follow-ups")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    out = out_dir()

    if args.cmd == "login":
        from .sharepoint import SharePointClient
        sp = SharePointClient(sharepoint_settings())
        sp.tokens.graph_token()
        sp.tokens.sharepoint_token()
        web = sp.web()
        print(f"Signed in. Source site: {web['Title']} ({web['Url']})")
    elif args.cmd == "extract":
        from .extract import extract
        from .sharepoint import SharePointClient
        summary = extract(SharePointClient(sharepoint_settings()), out, with_attachments=not args.no_attachments,
                          with_libraries=not args.no_libraries, max_file_mb=args.max_file_mb)
        print(json.dumps(summary, indent=2))
    elif args.cmd == "plan":
        from .plan import build_plan, load_mapping
        plan = build_plan(out, load_mapping(args.mapping))
        print(f"Wrote {out / 'plan.json'} and {out / 'plan.md'}")
        print(f"{len(plan['report']['warnings'])} warnings, {len(plan['report']['manual'])} manual follow-ups")
    elif args.cmd == "load":
        from .liferay import LiferayClient
        from .load import Loader
        if args.dry_run:
            (out / "dry-run.jsonl").unlink(missing_ok=True)
        loader = Loader(LiferayClient(liferay_settings(), dry_run=args.dry_run, out=out), out)
        loader.run(args.phase or PHASES)
        st = loader.state
        print(f"Done. {len(st.get('errors', []))} errors, {len(st.get('manual', []))} manual follow-ups in {loader.state_file}")
    elif args.cmd == "verify":
        from .liferay import LiferayClient
        from .verify import verify
        rep = verify(LiferayClient(liferay_settings()), out)
        bad = [r for r in rep["rows"] if not r["ok"]]
        print(f"Wrote {out / 'verify.md'}: {len(rep['rows']) - len(bad)}/{len(rep['rows'])} checks match")


if __name__ == "__main__":
    main()
