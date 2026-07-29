"""Stamp a version query onto the static clients' asset URLs.

GitHub Pages serves everything under ``clients/`` with ``cache-control:
max-age=600``.  A browser that loaded the page within the last ten minutes
therefore keeps using its cached copy of the ES modules and the stylesheet
*without revalidating* -- so a freshly deployed fix silently does not reach
players until the TTL expires.  A plain reload does not help either: browsers
revalidate the top-level document on reload but still serve subresources from
cache while they are fresh.

Appending ``?v=<release>`` to every asset URL makes each release a distinct
cache key.  The reloaded HTML then points at URLs the browser has never seen,
so the new modules are fetched immediately.

The clients are plain static sites with no build step, so this runs over the
assembled Pages artifact in CI rather than over the checked-in sources.  Their
imports are deliberately uniform -- every one is a static, relative specifier
ending in ``.js`` -- which is what makes the rewrite safe; see
``tests/unit/clients/test_stamp_assets.py`` for the shapes that are covered.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# `import ... from "./x.js"`, `export ... from "../y.js"`.  Only relative
# specifiers are touched: bare specifiers would be import-map entries and
# absolute URLs point off-site.  A specifier that already carries a query is
# skipped by the `[^"'?]` class so the pass is idempotent.
_JS_IMPORT = re.compile(r"""(?P<head>\bfrom\s*["'])(?P<path>\.{1,2}/[^"'?]*\.js)(?P<tail>["'])""")

# `<script src="src/main.js">`, `<link href="style.css">`.  Anchored on the
# attribute so plain text mentioning a filename is left alone.
_HTML_ASSET = re.compile(
    r"""(?P<head>\b(?:src|href)\s*=\s*")(?P<path>(?!https?://|//|/)[^"?#]*\.(?:js|css))(?P<tail>")"""
)


def _stamp(text: str, pattern: re.Pattern[str], version: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{m['head']}{m['path']}?v={version}{m['tail']}"

    return pattern.sub(repl, text), count


def stamp_tree(root: pathlib.Path, version: str) -> tuple[int, int]:
    """Rewrite every asset URL under *root* in place.  Returns (files, urls)."""
    files = urls = 0
    for path in sorted(root.rglob("*")):
        if path.suffix == ".js":
            pattern = _JS_IMPORT
        elif path.suffix == ".html":
            pattern = _HTML_ASSET
        else:
            continue
        original = path.read_text(encoding="utf-8")
        stamped, count = _stamp(original, pattern, version)
        if count:
            path.write_text(stamped, encoding="utf-8")
            files += 1
            urls += count
    return files, urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=pathlib.Path, help="assembled site directory")
    parser.add_argument("version", help="release identifier, e.g. the short commit sha")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    # The version lands verbatim in a URL query, so keep it to characters that
    # need no escaping -- a stray `#` or `&` would truncate every import.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.version):
        parser.error(f"version must be alphanumeric/._-: {args.version!r}")

    files, urls = stamp_tree(args.root, args.version)
    print(f"stamped {urls} asset URLs across {files} files with ?v={args.version}")
    # A silent no-op would ship an unversioned site and reintroduce the very
    # staleness this guards against, so fail loudly instead.
    if not urls:
        print("error: no asset URLs found -- refusing to deploy unstamped", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
