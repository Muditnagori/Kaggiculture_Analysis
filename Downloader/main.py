"""Command Line Interface entry point for K2 Kaggle Replay Downloader.

Usage examples:
    # Open interactive menu:
    python main.py

    # Download Top 5 players and all their matches:
    python main.py --players 5 --matches all --outcome all

    # Download Top 20 players and all their matches:
    python main.py --top-20 --matches all --outcome all

    # Quick download using preset player count & settings from config.json:
    python main.py --quick

    # Edit default settings & presets interactively:
    python main.py --edit

    # Download replays for a specific player by username:
    python main.py --username "player123" --matches all --outcome all

    # Interactive Kaggle Login:
    python main.py --login
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure current directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from auth import interactive_login
    from downloader import K2Downloader
    from history import get_history_stats
except ImportError:
    from .auth import interactive_login
    from .downloader import K2Downloader
    from .history import get_history_stats


CONFIG_FILE = BASE_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "competition": "kaggriculture",
        "top_percentage": 10.0,
        "quick_players_count": 5,
        "num_players": 5,
        "matches_per_player": -1,
        "outcome_filter": "all",
        "batch_size": 5,
        "output_dir": "downloads",
        "auto_zip": True,
    }


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def parse_matches(val: str | int | None) -> int:
    """Parses match count string, supporting 'all', '-1', etc."""
    if val is None:
        return -1
    s = str(val).strip().lower()
    if s in ("all", "none", "-1", "*"):
        return -1
    try:
        return int(s)
    except ValueError:
        return -1


def format_matches_label(val: any) -> str:
    m = parse_matches(val)
    return "ALL" if m <= 0 else str(m)


def interactive_edit_config() -> None:
    """Interactively edit preset options 1 and 2 and save to config.json."""
    config = load_config()
    print("\n" + "=" * 65, flush=True)
    print("      EDIT DOWNLOAD SETTINGS (PERCENTAGE, PLAYERS, MATCHES)", flush=True)
    print("=" * 65, flush=True)
    print("Press [Enter] on any option to keep its current value.\n", flush=True)

    # 1. Competition
    curr_comp = config.get("competition", "kaggriculture")
    val = input(f"Default Competition [{curr_comp}]: ").strip()
    if val:
        config["competition"] = val

    # 2. Option 1: Top Percentage
    curr_pct = config.get("top_percentage", 10.0)
    val = input(f"Option 1 - Top Percentage % [{curr_pct}%]: ").strip()
    if val:
        try:
            config["top_percentage"] = float(val.replace("%", ""))
        except ValueError:
            print("Invalid percentage; keeping current value.")

    # 3. Option 2: Quick Download Player Count
    curr_quick = config.get("quick_players_count", 5)
    val = input(f"Option 2 - Quick Download Player Count [{curr_quick} players]: ").strip()
    if val:
        try:
            config["quick_players_count"] = int(val)
        except ValueError:
            print("Invalid player count; keeping current value.")

    # 4. Matches per player
    curr_matches = format_matches_label(config.get("matches_per_player", -1))
    val = input(f"Matches Per Player (number or 'all') [{curr_matches} matches]: ").strip()
    if val:
        config["matches_per_player"] = parse_matches(val)

    # 5. Outcome filter
    curr_outcome = config.get("outcome_filter", "all")
    val = input(f"Outcome Filter (all / win / lose / draw) [{curr_outcome}]: ").strip().lower()
    if val in ("win", "all", "lose", "draw"):
        config["outcome_filter"] = val

    save_config(config)
    print("\n" + "-" * 65, flush=True)
    print("Settings updated and saved to config.json successfully!", flush=True)
    print(f"  * Competition         : {config.get('competition')}", flush=True)
    print(f"  * Option 1 Percentage : Top {config.get('top_percentage')}% players", flush=True)
    print(f"  * Option 2 Quick Count: Top {config.get('quick_players_count')} players", flush=True)
    print(f"  * Matches Per Player  : {format_matches_label(config.get('matches_per_player'))} matches", flush=True)
    print(f"  * Outcome Filter      : {config.get('outcome_filter')}", flush=True)
    print("=" * 65 + "\n", flush=True)


def interactive_player_download() -> None:
    """Prompt user to select a specific player by username and download replays."""
    config = load_config()
    print("\n" + "=" * 65, flush=True)
    print("             SPECIFIC PLAYER REPLAY DOWNLOAD", flush=True)
    print("=" * 65 + "\n", flush=True)

    comp = input(f"Enter competition slug or ID [default: {config.get('competition', 'kaggriculture')}]: ").strip()
    if not comp:
        comp = config.get("competition", "kaggriculture")

    player_name = ""
    while not player_name:
        player_name = input("Enter player username or team name: ").strip()
        if not player_name:
            print("Player username/team name cannot be empty. Please enter a valid name.")

    default_matches_lbl = format_matches_label(config.get("matches_per_player", -1))
    matches_str = input(f"How many match replays for this player (number or 'all') [default: {default_matches_lbl}]: ").strip()
    matches_count = parse_matches(matches_str) if matches_str else parse_matches(config.get("matches_per_player", -1))

    outcome = input(f"Filter outcome (all / win / lose / draw) [default: {config.get('outcome_filter', 'all')}]: ").strip().lower()
    if outcome not in ("win", "all", "lose", "draw"):
        outcome = config.get("outcome_filter", "all")

    output_path = BASE_DIR / config.get("output_dir", "downloads")

    downloader = K2Downloader(
        competition=comp,
        top_percentage=None,
        num_players=None,
        player_name=player_name,
        matches_per_player=matches_count,
        outcome_filter=outcome,
        batch_size=config.get("batch_size", 5),
        output_dir=output_path,
        auto_zip=config.get("auto_zip", True),
    )
    asyncio.run(downloader.run())


def interactive_custom_download() -> None:
    """Prompt user for custom parameters on the fly."""
    config = load_config()
    print("\n" + "=" * 65, flush=True)
    print("                   CUSTOM DOWNLOAD SETUP", flush=True)
    print("=" * 65 + "\n", flush=True)

    comp = input(f"Enter competition slug or ID [default: {config.get('competition', 'kaggriculture')}]: ").strip()
    if not comp:
        comp = config.get("competition", "kaggriculture")

    num_players_str = input("How many top players to download [e.g. 5, or press Enter for % cutoff]: ").strip()
    players_count = int(num_players_str) if num_players_str.isdigit() else None

    default_matches_lbl = format_matches_label(config.get("matches_per_player", -1))
    matches_str = input(f"How many match replays per player (number or 'all') [default: {default_matches_lbl}]: ").strip()
    matches_count = parse_matches(matches_str) if matches_str else parse_matches(config.get("matches_per_player", -1))

    outcome = input(f"Filter outcome (all / win / lose / draw) [default: {config.get('outcome_filter', 'all')}]: ").strip().lower()
    if outcome not in ("win", "all", "lose", "draw"):
        outcome = config.get("outcome_filter", "all")

    output_path = BASE_DIR / config.get("output_dir", "downloads")

    downloader = K2Downloader(
        competition=comp,
        top_percentage=config.get("top_percentage", 10.0) if players_count is None else None,
        num_players=players_count,
        matches_per_player=matches_count,
        outcome_filter=outcome,
        batch_size=config.get("batch_size", 5),
        output_dir=output_path,
        auto_zip=config.get("auto_zip", True),
    )
    asyncio.run(downloader.run())



def show_download_history() -> None:
    """Displays formatted statistics from the persistent SQLite download history database."""
    try:
        stats = get_history_stats()
        print("\n" + "=" * 65, flush=True)
        print("          K2 PERSISTENT DOWNLOAD HISTORY & MATCH REGISTRY", flush=True)
        print("=" * 65, flush=True)
        print(f"  • Total Matches Recorded : {stats['total_episodes']:,}", flush=True)
        print(f"  • Unique RNG Match Seeds : {stats['unique_seeds']:,}", flush=True)
        print(f"  • Raw JSONs on Disk      : {stats['raw_files_existing']:,}", flush=True)
        print(f"  • Unique Players Tracked : {stats['unique_players']:,}", flush=True)
        print(f"  • Database File Size     : {stats['db_size_kb']:.2f} KB", flush=True)
        print(f"  • Database File Path     : {stats['db_path']}", flush=True)
        print("=" * 65 + "\n", flush=True)
    except Exception as e:
        print(f"[!] Error retrieving download history: {e}", flush=True)


def interactive_menu():
    """Display interactive menu showing exact percentage, player count, and matches."""
    while True:
        config = load_config()
        pct = config.get("top_percentage", 10.0)
        quick_cnt = config.get("quick_players_count", 5)
        matches = config.get("matches_per_player", -1)
        matches_lbl = format_matches_label(matches)
        outcome = config.get("outcome_filter", "all")

        print("\n" + "=" * 65, flush=True)
        print("              K2 - KAGGLE REPLAY DOWNLOADER", flush=True)
        print("=" * 65, flush=True)
        print(f"  1. Top {quick_cnt} Players Download   - Top {quick_cnt} players ({matches_lbl} {outcome} matches each)", flush=True)
        print(f"  2. Top 20 Players Download  - Top 20 players ({matches_lbl} {outcome} matches each)", flush=True)
        print(f"  3. Percentage Download      - Top {pct}% players ({matches_lbl} {outcome} matches each)", flush=True)
        print(f"  4. Specific Player          - Select player by username ({matches_lbl} {outcome} matches)", flush=True)
        print("  5. Custom Download          - Choose custom players, matches & competition", flush=True)
        print("  6. Edit Settings            - Change % for Opt 3, players for Opt 1, matches", flush=True)
        print("  7. View Download History    - Inspect recorded matches & seeds in database", flush=True)
        print("  8. Kaggle Re-Login          - Open Chrome to log into your Kaggle account", flush=True)
        print("  9. Exit                     - Close downloader", flush=True)
        print("=" * 65, flush=True)

        choice = input("Select an option [1-9, default 1]: ").strip()
        if not choice:
            choice = "1"

        if choice == "1":
            output_path = BASE_DIR / config.get("output_dir", "downloads")
            downloader = K2Downloader(
                competition=config.get("competition", "kaggriculture"),
                top_percentage=None,
                num_players=quick_cnt,
                matches_per_player=matches,
                outcome_filter=outcome,
                batch_size=config.get("batch_size", 5),
                output_dir=output_path,
                auto_zip=config.get("auto_zip", True),
            )
            asyncio.run(downloader.run())
            break

        elif choice == "2":
            output_path = BASE_DIR / config.get("output_dir", "downloads")
            downloader = K2Downloader(
                competition=config.get("competition", "kaggriculture"),
                top_percentage=None,
                num_players=20,
                matches_per_player=matches,
                outcome_filter=outcome,
                batch_size=config.get("batch_size", 5),
                output_dir=output_path,
                auto_zip=config.get("auto_zip", True),
            )
            asyncio.run(downloader.run())
            break

        elif choice == "3":
            output_path = BASE_DIR / config.get("output_dir", "downloads")
            downloader = K2Downloader(
                competition=config.get("competition", "kaggriculture"),
                top_percentage=pct,
                num_players=None,
                matches_per_player=matches,
                outcome_filter=outcome,
                batch_size=config.get("batch_size", 5),
                output_dir=output_path,
                auto_zip=config.get("auto_zip", True),
            )
            asyncio.run(downloader.run())
            break

        elif choice == "4":
            interactive_player_download()
            break

        elif choice == "5":
            interactive_custom_download()
            break

        elif choice == "6":
            interactive_edit_config()

        elif choice == "7":
            show_download_history()

        elif choice == "8":
            asyncio.run(interactive_login())

        elif choice == "9":
            print("\nExiting K2 Downloader. Goodbye!\n")
            break
        else:
            print("Invalid selection. Please enter a number between 1 and 8.")


def main():
    config = load_config()

    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        description="K2 — Download top players' match replays into player subfolders and structured ZIP."
    )
    parser.add_argument(
        "-c",
        "--competition",
        default=config.get("competition", "kaggriculture"),
        help="Kaggle competition slug or ID (default: from config.json)",
    )
    parser.add_argument(
        "-u",
        "--username",
        "--player",
        "--player-name",
        dest="username",
        default=None,
        help="Target a specific player by username or team name (e.g. -u 'player123')",
    )
    parser.add_argument(
        "-n",
        "--players",
        type=int,
        default=config.get("num_players", 5),
        help="Exact number of top players to download (e.g. 5). Overrides --top-percent if set.",
    )
    parser.add_argument(
        "-p",
        "--top-percent",
        type=float,
        default=config.get("top_percentage", 10.0),
        help="Top percentage of leaderboard teams (default: from config.json)",
    )
    parser.add_argument(
        "-m",
        "--matches",
        type=parse_matches,
        default=config.get("matches_per_player", -1),
        help="Match replays per player (number, or 'all' / -1 for all matches; default: from config.json)",
    )
    parser.add_argument(
        "--outcome",
        choices=["win", "all", "lose", "draw"],
        default=config.get("outcome_filter", "all"),
        help="Filter match outcomes (default: from config.json)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=config.get("output_dir", "downloads"),
        help="Output directory (default: downloads)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick download with preset quick_players_count from config.json",
    )
    parser.add_argument(
        "--top-20",
        "--top20",
        action="store_true",
        help="Download replays for the Top 20 players on the leaderboard",
    )
    parser.add_argument(
        "--edit",
        "--edit-config",
        action="store_true",
        help="Interactively edit Option 1 & 2 default presets in config.json",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Disable automatic ZIP archive creation",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Launch Chrome to re-login to your Kaggle account",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="View persistent download history registry statistics",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass download history check and force re-download of all matches",
    )

    args = parser.parse_args()

    if args.login:
        asyncio.run(interactive_login())
        return

    if args.history:
        show_download_history()
        return

    if args.edit:
        interactive_edit_config()
        return

    # Handle player count & targets
    players_count = args.players
    top_pct = args.top_percent
    target_player = args.username

    if target_player:
        top_pct = None
        players_count = None
    elif args.top_20:
        players_count = 20
        top_pct = None
    elif args.quick:
        players_count = config.get("quick_players_count", 5)
        top_pct = None
    elif players_count is not None:
        top_pct = None

    output_path = BASE_DIR / args.output if not Path(args.output).is_absolute() else Path(args.output)

    downloader = K2Downloader(
        competition=args.competition,
        top_percentage=top_pct,
        num_players=players_count,
        player_name=target_player,
        matches_per_player=args.matches,
        outcome_filter=args.outcome,
        batch_size=config.get("batch_size", 5),
        output_dir=output_path,
        auto_zip=not args.no_zip,
        force=args.force,
    )

    asyncio.run(downloader.run())


if __name__ == "__main__":
    main()
