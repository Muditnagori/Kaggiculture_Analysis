import sys
import json
from pathlib import Path
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from generate_player_reports import process_player_folder

def main():
    folder = Path("Kaggiculture_Analysis/Downloader/downloads/kaggriculture/01_Boey")
    folder_name, results = process_player_folder(folder, max_workers=6)

    matches = []
    for r in results:
        full_hash = None
        for sw, lw, ch, lh in r.route_hashes:
            if sw == 0 and lw == 10:
                full_hash = ch
                break
        matches.append({
            'episode_id': r.episode_id,
            'player_index': r.player_index,
            'opponent': r.opponent_name,
            'result': r.result,
            'reward': r.player_reward,
            'opp_reward': r.opponent_reward,
            'margin': r.margin,
            'shops': r.shop_sequence,
            'hash': full_hash,
            'first_crop': r.opening_crops_planted[0] if r.opening_crops_planted else 'NONE',
            'opening_animals': r.opening_animals_bought,
            'opening_buys': r.opening_market_buys,
            'last_invest': r.last_investment_step,
        })

    hash_counts = Counter(m['hash'] for m in matches)
    top_hash, top_count = hash_counts.most_common(1)[0]

    top_group = [m for m in matches if m['hash'] == top_hash]
    other_group = [m for m in matches if m['hash'] != top_hash]

    print(f"=== TOP REUSED ROUTE GROUP ({len(top_group)} matches) ===")
    print("Player Seats:", Counter(m['player_index'] for m in top_group))
    print("Win/Loss Record:", Counter(m['result'] for m in top_group))
    print("Avg Score:", f"${sum(m['reward'] for m in top_group)/len(top_group):,.2f}")
    print("Avg Opp Score:", f"${sum(m['opp_reward'] for m in top_group)/len(top_group):,.2f}")
    print("First Crop Planted:", Counter(m['first_crop'] for m in top_group))
    print("Opening Animals:", Counter(tuple(m['opening_animals']) for m in top_group))
    print("Opening Market Buys:", Counter(tuple(m['opening_buys']) for m in top_group))
    print("Top 5 Shop Sequences:", Counter(tuple(m['shops']) for m in top_group).most_common(5))
    print("Top 5 Opponents:", Counter(m['opponent'] for m in top_group).most_common(5))

    print(f"\n=== OTHER ROUTES GROUP ({len(other_group)} matches) ===")
    print("Player Seats:", Counter(m['player_index'] for m in other_group))
    print("Win/Loss Record:", Counter(m['result'] for m in other_group))
    print("Avg Score:", f"${sum(m['reward'] for m in other_group)/len(other_group):,.2f}")
    print("Avg Opp Score:", f"${sum(m['opp_reward'] for m in other_group)/len(other_group):,.2f}")
    print("First Crop Planted:", Counter(m['first_crop'] for m in other_group).most_common(5))
    print("Opening Animals:", Counter(tuple(m['opening_animals']) for m in other_group).most_common(5))
    print("Opening Market Buys:", Counter(tuple(m['opening_buys']) for m in other_group).most_common(5))
    print("Top 5 Shop Sequences:", Counter(tuple(m['shops']) for m in other_group).most_common(5))
    print("Top 5 Opponents:", Counter(m['opponent'] for m in other_group).most_common(5))

if __name__ == '__main__':
    main()
