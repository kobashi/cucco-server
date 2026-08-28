"""ゼミ生が置いたAI方策の読み込み(cucco.ai.plugins)。

**壊れたファイルでサーバーを止めない**ことと、**他人の名前を奪わせない**ことが
この仕組みの肝なので、そこを固定する。
"""

import pytest

from cucco.ai.plugins import load_policy_dir
from cucco.ai.policies import POLICIES, BasePolicy


@pytest.fixture(autouse=True)
def restore_registry():
    """テストが POLICIES を汚さないように、元に戻す。"""
    before = dict(POLICIES)
    yield
    POLICIES.clear()
    POLICIES.update(before)


def write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


GOOD = '''
from cucco.ai.policies import BasePolicy


class Mine(BasePolicy):
    """いつも交換する(テスト用)"""

    name = "zemi_test_one"

    def decide_change(self, own_rank, alive_count):
        return True
'''


def test_a_dropped_in_file_becomes_a_usable_policy(tmp_path):
    write(tmp_path, "mine.py", GOOD)
    report = load_policy_dir(tmp_path)

    assert [p.name for p in report.loaded] == ["zemi_test_one"]
    assert report.loaded[0].description == "いつも交換する(テスト用)"
    assert POLICIES["zemi_test_one"]().decide_change("3", 4) is True


def test_a_broken_file_is_skipped_and_the_rest_still_load(tmp_path):
    """書きかけの1ファイルで、その日の対局が全部止まってはいけない。"""
    write(tmp_path, "broken.py", "this is not python(")
    write(tmp_path, "mine.py", GOOD)

    report = load_policy_dir(tmp_path)

    assert [p.name for p in report.loaded] == ["zemi_test_one"]
    assert [f.source.name for f in report.failures] == ["broken.py"]
    assert "読み込み中にエラー" in report.failures[0].reason


def test_a_name_already_in_use_is_refused(tmp_path):
    """上書きを許すと、成績が別人の方策に混ざる。"""
    write(tmp_path, "clash.py", GOOD.replace("zemi_test_one", "matrix"))
    original = POLICIES["matrix"]

    report = load_policy_dir(tmp_path)

    assert report.loaded == []
    assert "既に使われている" in report.failures[0].reason
    assert POLICIES["matrix"] is original


@pytest.mark.parametrize("bad", ["", "A", "Yamada", "my policy", "x" * 33])
def test_unusable_names_are_refused(tmp_path, bad):
    write(tmp_path, "named.py", GOOD.replace("zemi_test_one", bad))
    report = load_policy_dir(tmp_path)
    assert report.loaded == []
    assert report.failures


def test_a_file_without_a_policy_is_reported(tmp_path):
    write(tmp_path, "notes.py", "VALUE = 1\n")
    report = load_policy_dir(tmp_path)
    assert report.loaded == []
    assert "BasePolicy を継承したクラスが見つからない" in report.failures[0].reason


def test_importing_someone_elses_policy_does_not_register_it(tmp_path):
    """`from ... import MatrixPolicy` しただけの名前を拾わない。"""
    write(
        tmp_path,
        "borrow.py",
        "from cucco.ai.policies import MatrixPolicy, BasePolicy\n"
        "\n"
        "class Mine(BasePolicy):\n"
        '    """借り物ではない自分の方策"""\n'
        '    name = "zemi_test_two"\n'
        "    def decide_change(self, own_rank, alive_count):\n"
        "        return False\n",
    )
    report = load_policy_dir(tmp_path)
    assert [p.name for p in report.loaded] == ["zemi_test_two"]


def test_files_starting_with_underscore_are_left_alone(tmp_path):
    write(tmp_path, "_shared.py", GOOD)
    assert load_policy_dir(tmp_path).loaded == []


def test_a_missing_directory_is_not_an_error(tmp_path):
    report = load_policy_dir(tmp_path / "nope")
    assert report.loaded == [] and report.failures == []


def test_the_example_in_the_docs_actually_loads():
    """手順書が指す見本が壊れていると、最初の一歩で詰まる。"""
    from pathlib import Path

    report = load_policy_dir(Path("docs/examples"))
    assert [p.name for p in report.loaded] == ["example_weak_change"]
    assert issubclass(POLICIES["example_weak_change"], BasePolicy)
