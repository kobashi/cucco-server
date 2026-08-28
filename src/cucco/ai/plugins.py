"""ゼミ生が書いたAI方策を、ファイルを置くだけで使えるようにする仕組み。

サーバーのコードには手を入れない。指定したフォルダ(既定 `policies/`)に
`.py` を置き、`BasePolicy` を継承したクラスを1つ以上定義すれば、その名前で
卓に座らせられるようになる(`create_table` の `ai_players`)。

**なぜ「読み込みに失敗しても起動する」のか**: このフォルダの中身はゼミ生が
書いたコードで、壊れていることがある。1つの書きかけのファイルでサーバー全体が
起動しなくなると、その日の対局が全部止まる。読めなかったものは名前を挙げて
警告し、残りで動く。

**なぜ名前の衝突を拒むのか**: 同じ名前で上書きできてしまうと、成績が別人の
方策に混ざる(結果は方策名で記録される)。既存と衝突した方策は登録しない。
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from cucco.ai.policies import POLICIES, BasePolicy

logger = logging.getLogger("cucco.ai.plugins")

# 方策名に許す文字。成績のキーとして残り、卓の作成フォームにも並ぶので、
# 記号や空白は入れさせない。
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,31}$")


@dataclass(frozen=True)
class LoadedPolicy:
    name: str
    source: Path
    description: str


@dataclass(frozen=True)
class LoadFailure:
    source: Path
    reason: str


@dataclass(frozen=True)
class LoadReport:
    loaded: list[LoadedPolicy]
    failures: list[LoadFailure]


def _describe(cls: type) -> str:
    doc = inspect.getdoc(cls) or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _policies_in(module) -> list[type]:
    """そのファイルで**定義された** BasePolicy のサブクラス。

    import しただけの既存方策(`from cucco.ai.policies import MatrixPolicy`)を
    拾ってしまうと、他人の方策を自分の名前で登録したことになるので、
    定義元がこのモジュールのものだけを見る。
    """
    return [
        obj
        for _, obj in vars(module).items()
        if inspect.isclass(obj)
        and issubclass(obj, BasePolicy)
        and obj is not BasePolicy
        and obj.__module__ == module.__name__
    ]


def _load_file(path: Path) -> tuple[list[LoadedPolicy], list[LoadFailure]]:
    module_name = f"cucco_policy_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return [], [LoadFailure(path, "Pythonのモジュールとして読み込めない")]
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # 書きかけのファイルでサーバーを止めない
        sys.modules.pop(module_name, None)
        return [], [LoadFailure(path, f"読み込み中にエラー: {type(exc).__name__}: {exc}")]

    classes = _policies_in(module)
    if not classes:
        return [], [LoadFailure(path, "BasePolicy を継承したクラスが見つからない")]

    loaded: list[LoadedPolicy] = []
    failures: list[LoadFailure] = []
    for cls in classes:
        name = getattr(cls, "name", None)
        if not isinstance(name, str) or not NAME_RE.match(name):
            failures.append(
                LoadFailure(path, f"{cls.__name__}: name は英小文字・数字・_ で2〜32文字(いまは {name!r})")
            )
            continue
        if name in POLICIES:
            failures.append(LoadFailure(path, f"{cls.__name__}: 方策名 {name!r} は既に使われている"))
            continue
        POLICIES[name] = cls
        loaded.append(LoadedPolicy(name=name, source=path, description=_describe(cls)))
    return loaded, failures


def load_policy_dir(directory: Path) -> LoadReport:
    """`directory` の直下の `.py` を読み、方策を POLICIES に登録する。

    `_` で始まるファイルは読まない(共通処理の置き場所として使えるように)。
    """
    loaded: list[LoadedPolicy] = []
    failures: list[LoadFailure] = []
    if not directory.is_dir():
        return LoadReport(loaded, failures)
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        got, bad = _load_file(path)
        loaded.extend(got)
        failures.extend(bad)
    return LoadReport(loaded, failures)


def log_report(report: LoadReport, directory: Path) -> None:
    for entry in report.loaded:
        logger.info("loaded policy %s from %s", entry.name, entry.source.name)
    for failure in report.failures:
        logger.warning("skipped %s: %s", failure.source.name, failure.reason)
    if not report.loaded and not report.failures:
        logger.info("no add-on policies in %s", directory)
