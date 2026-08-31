"""Entry point: ./run-tests"""
import argparse
import sys

from .harness import discover, run

ap = argparse.ArgumentParser(prog="run-tests", description="crystal-pilot regression suite")
ap.add_argument("-k", "--filter", default=None, help="only run tests matching this regex")
ap.add_argument("-v", "--verbose", action="store_true", help="show notes and tracebacks")
ap.add_argument("--build-fixtures", nargs="*", default=None, metavar="NAME",
                help="(re)build save-state fixtures, then exit")
ap.add_argument("--self-check", action="store_true",
                help="re-introduce known bugs and confirm the suite catches them")
args = ap.parse_args()

if args.self_check:
    from .selfcheck import self_check
    sys.exit(self_check())

if args.build_fixtures is not None:
    from .build_fixtures import build
    sys.exit(build(args.build_fixtures or None))

discover()
sys.exit(run(pattern=args.filter, verbose=args.verbose))
