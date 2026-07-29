import pathlib
import re
import shutil

import pytest

from tools.stamp_assets import main, stamp_tree

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# Kept in step with the assembly step in .github/workflows/deploy-pages.yml.
CLIENTS = ["index.html", "web", "play", "web-common"]


@pytest.fixture
def site(tmp_path):
    """The real clients tree, assembled exactly as the Pages workflow does."""
    root = tmp_path / "_site"
    root.mkdir()
    for name in CLIENTS:
        source = REPO_ROOT / "clients" / name
        if source.is_dir():
            shutil.copytree(source, root / name)
        else:
            shutil.copy(source, root)
    return root


def test_no_relative_asset_url_is_left_unstamped(site):
    # Guards against an import shape the rewrite does not recognise: a missed
    # specifier would keep serving a stale module after a release.
    stamp_tree(site, "abc1234")

    missed = []
    for path in sorted(site.rglob("*")):
        if path.suffix not in (".js", ".html"):
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""\bfrom\s*["'](\.{1,2}/[^"']*\.js)["']""", text):
            if "?v=" not in m.group(1):
                missed.append(f"{path.relative_to(site)}: {m.group(1)}")
        for m in re.finditer(r"""\b(?:src|href)\s*=\s*"((?!https?://|//|/)[^"]*\.(?:js|css))\"""", text):
            if "?v=" not in m.group(1):
                missed.append(f"{path.relative_to(site)}: {m.group(1)}")
    assert not missed, "unstamped asset URLs: " + ", ".join(missed)


def test_stamps_the_play_client_entry_points(site):
    stamp_tree(site, "abc1234")

    index = (site / "play" / "index.html").read_text(encoding="utf-8")
    assert 'src="src/main.js?v=abc1234"' in index
    assert 'href="style.css?v=abc1234"' in index

    main_js = (site / "play" / "src" / "main.js").read_text(encoding="utf-8")
    assert 'from "./gameState.js?v=abc1234"' in main_js
    assert 'from "../../web-common/cards.js?v=abc1234"' in main_js


def test_leaves_absolute_and_off_site_urls_alone(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(
        '<link href="https://cdn.example//a.css">'
        '<script src="//cdn.example/b.js"></script>'
        '<script src="/rooted.js"></script>',
        encoding="utf-8",
    )
    stamp_tree(tmp_path, "v1")
    assert "?v=" not in page.read_text(encoding="utf-8")


def test_leaves_bare_specifiers_alone(tmp_path):
    module = tmp_path / "m.js"
    module.write_text('import x from "lodash-es/x.js";\n', encoding="utf-8")
    stamp_tree(tmp_path, "v1")
    assert "?v=" not in module.read_text(encoding="utf-8")


def test_is_idempotent(tmp_path):
    module = tmp_path / "m.js"
    module.write_text('import x from "./x.js";\n', encoding="utf-8")
    stamp_tree(tmp_path, "v1")
    once = module.read_text(encoding="utf-8")
    stamp_tree(tmp_path, "v2")
    assert module.read_text(encoding="utf-8") == once


def test_the_string_it_stamps_is_not_confused_by_prose(tmp_path):
    module = tmp_path / "m.js"
    module.write_text('// see ./notes.js for why\nconst s = "./data.js";\n', encoding="utf-8")
    stamp_tree(tmp_path, "v1")
    assert "?v=" not in module.read_text(encoding="utf-8")


def test_cli_rejects_a_version_that_would_break_the_query(tmp_path, capsys):
    (tmp_path / "m.js").write_text('import x from "./x.js";\n', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path), "bad#version"])
    assert excinfo.value.code != 0


def test_cli_fails_when_nothing_was_stamped(tmp_path):
    # An empty result means the assembly step moved and the site would deploy
    # unversioned -- the failure mode this whole guard exists to prevent.
    (tmp_path / "readme.txt").write_text("no assets here", encoding="utf-8")
    assert main([str(tmp_path), "abc1234"]) == 1
