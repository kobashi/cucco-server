"""成績確認CLI (AIロードマップ第3段階).

Read-only viewer over the results database the server writes
(docs/protocol/design.md 「永続化・成績記録」). Run on the server machine:

    python -m cucco.tools.stats                      # 通算成績(名前別)
    python -m cucco.tools.stats --policies           # 内蔵AI方策別の成績
    python -m cucco.tools.stats --player たろう       # 1人の直近ゲーム
    python -m cucco.tools.stats --recent 5           # 直近ゲームの結果一覧
    python -m cucco.tools.stats --evaluations        # 評価モードの実行一覧
    python -m cucco.tools.stats --periods            # 集計期間の一覧
    python -m cucco.tools.stats --period 3           # その集計期間だけを集計
    python -m cucco.tools.stats --period all         # 全期間の通算
    python -m cucco.tools.stats --db data/results.db # DBファイル指定

集計期間を指定しない場合は開催中の期間(管理コンソールで「〆る」まで)を
集計する。〆た期間の記録は消えないので、`--period` で後から見返せる。

Output goes to the terminal only. NOTE (運用): 出力にはプレイヤーの表示名
(ゼミ生の実名の場合がある)が含まれる。集計結果をファイル化しても公開
リポジトリにはコミットしないこと。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cucco.persistence import stats


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    def fmt(row):  # noqa: E306
        return "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
    lines = [fmt(headers), fmt(["-" * w for w in widths])]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


def _career_table(rows) -> str:
    if not rows:
        return "(記録がありません)"
    return _table(
        ["名前", "種別", "対局数", "勝利", "勝率", "平均順位", "平均チップ", "最終対局"],
        [
            [
                c.name,
                c.ai_policy or c.player_type,
                c.games,
                c.wins,
                f"{c.win_rate:.3f}",
                f"{c.avg_rank:.2f}",
                f"{c.avg_chips:.1f}",
                (c.last_played or "")[:19],
            ]
            for c in rows
        ],
    )


def _period_banner(conn, period_id: int | None) -> str:
    """One line naming the 集計期間 the numbers below cover -- without it the
    same command run before and after a 〆 prints two different tables with
    nothing on screen to explain why."""
    if period_id is None:
        return "[全期間の通算]"
    for p in stats.list_periods(conn):
        if p["id"] == period_id:
            state = "〆済み" if p["closed_at"] else "開催中"
            return f"[{p['name']} / {p['started_at'][:16]}〜 / {state}]"
    return f"[集計期間 {period_id}]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cucco-server 成績確認ツール(読み取り専用)")
    parser.add_argument("--db", type=Path, default=Path("data/results.db"), help="結果DBのパス(デフォルト: data/results.db)")
    parser.add_argument("--mode", choices=["normal", "evaluation"], help="モードで絞り込む")
    parser.add_argument("--policies", action="store_true", help="内蔵AI方策別の通算成績")
    parser.add_argument("--player", help="この名前(全半角・大文字小文字は同一視)の直近ゲームを表示")
    parser.add_argument("--recent", type=int, metavar="N", help="直近Nゲームの結果一覧")
    parser.add_argument("--evaluations", action="store_true", help="評価モードの実行サマリ一覧")
    parser.add_argument("--periods", action="store_true", help="集計期間の一覧")
    parser.add_argument(
        "--period", default="current", metavar="ID",
        help="集計期間(ID / current=開催中 / all=全期間通算。既定: current)",
    )
    args = parser.parse_args(argv)

    try:
        conn = stats.open_readonly(args.db)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        try:
            period_id = stats.resolve_period(conn, args.period)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        if args.periods:
            periods = stats.list_periods(conn)
            if not periods:
                print("(集計期間の記録がありません。サーバーを一度起動すると作成されます)")
                return 0
            print("集計期間:")
            print(
                _table(
                    ["ID", "期間", "開始", "終了", "対局数"],
                    [
                        [p["id"], p["name"], p["started_at"][:16], p["closed_at"][:16] if p["closed_at"] else "(開催中)", p["games"]]
                        for p in periods
                    ],
                )
            )
            return 0

        print(_period_banner(conn, period_id))
        if args.player:
            rows = stats.player_games(conn, args.player, period_id=period_id)
            if not rows:
                print(f"「{args.player}」の対局記録はありません")
                return 0
            print(f"「{args.player}」の直近{len(rows)}ゲーム:")
            print(
                _table(
                    ["終了時刻", "卓", "モード", "順位", "人数", "チップ"],
                    [[r["ended_at"][:19], r["table_id"], r["mode"], r["final_rank"], r["field_size"], r["final_chips"]] for r in rows],
                )
            )
        elif args.recent is not None:
            for entry in stats.recent_games(conn, limit=args.recent, period_id=period_id):
                g = entry["game"]
                print(f"\n[{g['ended_at'][:19]}] 卓 {g['table_id']} ({g['mode']})")
                print(
                    _table(
                        ["順位", "名前", "種別", "チップ"],
                        [[s["final_rank"], s["name"], s["ai_policy"] or s["player_type"], s["final_chips"]] for s in entry["standings"]],
                    )
                )
        elif args.evaluations:
            runs = stats.evaluation_runs(conn, period_id=period_id)
            if not runs:
                print("(評価モードの記録がありません)")
                return 0
            for run in runs:
                print(f"\n[{run['recorded_at'][:19]}] 卓 {run['table_id']} — {run['games_played']}/{run['game_count']}ゲーム")
                players = run["summary"].get("players", {})
                print(
                    _table(
                        ["player_id", "勝率", "平均順位", "失格率"],
                        [
                            [pid[:12], f"{st['win_rate']:.3f}", f"{st['avg_rank']:.2f}", f"{st['disqualification_rate']:.3f}"]
                            for pid, st in players.items()
                        ],
                    )
                )
        elif args.policies:
            print("内蔵AI方策別の通算成績:")
            print(_career_table(stats.career_by_policy(conn, mode=args.mode, period_id=period_id)))
        else:
            print("プレイヤー別の通算成績(名前で集計):")
            print(_career_table(stats.career_by_name(conn, mode=args.mode, period_id=period_id)))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
