"""成績確認CLI (AIロードマップ第3段階).

Read-only viewer over the results database the server writes
(docs/protocol/design.md 「永続化・成績記録」). Run on the server machine:

    python -m cucco.tools.stats                      # 通算成績(名前別)
    python -m cucco.tools.stats --policies           # 内蔵AI方策別の成績
    python -m cucco.tools.stats --player たろう       # 1人の直近ゲーム
    python -m cucco.tools.stats --recent 5           # 直近ゲームの結果一覧
    python -m cucco.tools.stats --evaluations        # 評価モードの実行一覧
    python -m cucco.tools.stats --db data/results.db # DBファイル指定

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cucco-server 成績確認ツール(読み取り専用)")
    parser.add_argument("--db", type=Path, default=Path("data/results.db"), help="結果DBのパス(デフォルト: data/results.db)")
    parser.add_argument("--mode", choices=["normal", "evaluation"], help="モードで絞り込む")
    parser.add_argument("--policies", action="store_true", help="内蔵AI方策別の通算成績")
    parser.add_argument("--player", help="この名前(全半角・大文字小文字は同一視)の直近ゲームを表示")
    parser.add_argument("--recent", type=int, metavar="N", help="直近Nゲームの結果一覧")
    parser.add_argument("--evaluations", action="store_true", help="評価モードの実行サマリ一覧")
    args = parser.parse_args(argv)

    try:
        conn = stats.open_readonly(args.db)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        if args.player:
            rows = stats.player_games(conn, args.player)
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
            for entry in stats.recent_games(conn, limit=args.recent):
                g = entry["game"]
                print(f"\n[{g['ended_at'][:19]}] 卓 {g['table_id']} ({g['mode']})")
                print(
                    _table(
                        ["順位", "名前", "種別", "チップ"],
                        [[s["final_rank"], s["name"], s["ai_policy"] or s["player_type"], s["final_chips"]] for s in entry["standings"]],
                    )
                )
        elif args.evaluations:
            runs = stats.evaluation_runs(conn)
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
            print(_career_table(stats.career_by_policy(conn, mode=args.mode)))
        else:
            print("プレイヤー別の通算成績(名前で集計):")
            print(_career_table(stats.career_by_name(conn, mode=args.mode)))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
