"""High-performance direct HTTP client for Kaggle Simulation Replays.

Uses session cookies from `auth.json` to stream large replay JSONs directly
to disk with zero Node.js / Playwright IPC limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

logger = logging.getLogger("K2.client")

KAGGLE_BASE = "https://www.kaggle.com"
GET_LEADERBOARD_URL = "https://www.kaggle.com/api/i/competitions.LeaderboardService/GetLeaderboard"
LIST_EPISODES_URL = "https://www.kaggle.com/api/i/competitions.EpisodeService/ListEpisodes"

# SSL context for high performance
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _fetch_leaderboard_pw(competition_id: int, auth_file: Path | None = None) -> dict[str, Any] | None:
    """Fetch leaderboard using Playwright API client supporting modern HTTP/2 and TLS ALPN."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as pw:
            kwargs: dict[str, Any] = {}
            if auth_file and auth_file.exists() and auth_file.stat().st_size > 50:
                try:
                    kwargs["storage_state"] = str(auth_file)
                except Exception:
                    pass
            context = pw.request.new_context(**kwargs)
            resp = context.post(
                GET_LEADERBOARD_URL,
                data={
                    "competitionId": competition_id,
                    "leaderboardMode": "LEADERBOARD_MODE_DEFAULT",
                },
                headers={
                    "Referer": f"https://www.kaggle.com/competitions/{competition_id}/leaderboard",
                    "Accept": "application/json",
                },
                timeout=45000,
            )
            if resp.status == 200:
                return resp.json()
            logger.warning("Playwright GetLeaderboard returned status %s", resp.status)
    except Exception as e:
        logger.warning("Playwright GetLeaderboard error: %s", e)
    return None


class KaggleSession:
    """Direct HTTP session using authenticated cookies from auth.json."""

    def __init__(self, auth_file: Path | str):
        self.auth_file = Path(auth_file)
        self.cookies_dict: dict[str, str] = {}
        self.cookie_str: str = ""
        self.xsrf_token: str = ""
        self.build_hash: str = ""
        self.load_auth()

    def load_auth(self) -> None:
        """Load cookies and XSRF tokens from auth.json."""
        if not self.auth_file.exists():
            raise FileNotFoundError(f"Auth file not found at {self.auth_file}")

        with open(self.auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_cookies = data.get("cookies", [])
        self.cookies_dict = {
            c["name"]: c["value"]
            for c in raw_cookies
            if "kaggle.com" in c.get("domain", "")
        }
        self.cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies_dict.items()])
        self.xsrf_token = self.cookies_dict.get("XSRF-TOKEN", "")
        self.build_hash = self.cookies_dict.get("build-hash", "")

    def _get_headers(self, is_json: bool = True) -> dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Cookie": self.cookie_str,
        }
        if is_json:
            headers["Content-Type"] = "application/json"
            if self.xsrf_token:
                headers["x-xsrf-token"] = self.xsrf_token
            if self.build_hash:
                headers["x-kaggle-build-version"] = self.build_hash
        return headers

    def resolve_competition_id(self, comp_input: str | int) -> tuple[int, str]:
        """Resolve a competition slug or numeric ID to (kaggle_id, slug)."""
        comp_str = str(comp_input).strip()
        if comp_str.isdigit():
            return int(comp_str), f"competition_{comp_str}"

        slug = comp_str.lower()
        known_map = {
            "kaggriculture": 147734,
            "pokemon-tcg-ai-battle": 116727,
            "rock-paper-scissors": 22838,
            "connectx": 22378,
            "halite": 18011,
            "lux-ai-season-3": 79275,
            "lux-ai-season-2": 45040,
            "kore-2022": 35438,
            "santa-2024": 86326,
            "santa-2023": 65427,
        }
        if slug in known_map:
            return known_map[slug], slug

        url = f"https://www.kaggle.com/competitions/{slug}"
        req = urllib.request.Request(url, headers=self._get_headers(is_json=False))
        try:
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = (
                    re.search(r'"competitionId":\s*([0-9]+)', html, re.I)
                    or re.search(r'competitionId\s*=\s*([0-9]+)', html, re.I)
                    or re.search(r'"id":\s*([0-9]+).*?"type":\s*"COMPETITION"', html, re.I)
                )
                if m:
                    return int(m.group(1)), slug
        except Exception:
            pass

        # Fallback to direct GetCompetition API service
        try:
            comp_url = "https://www.kaggle.com/api/i/competitions.CompetitionService/GetCompetition"
            comp_payload = json.dumps({"competitionName": slug}).encode("utf-8")
            comp_req = urllib.request.Request(
                comp_url,
                data=comp_payload,
                headers=self._get_headers(is_json=True),
                method="POST",
            )
            with urllib.request.urlopen(comp_req, context=_SSL_CTX, timeout=15) as resp:
                comp_data = json.loads(resp.read().decode("utf-8"))
                if comp_data.get("id"):
                    return int(comp_data["id"]), slug
        except Exception:
            pass

        raise ValueError(f"Could not resolve Kaggle competition ID for '{slug}'")

    def fetch_leaderboard(self, competition_id: int) -> list[dict[str, Any]]:
        """Fetch full leaderboard using Playwright API client (handles HTTP/2 & modern TLS) with urllib fallback."""
        data = None

        # 1. Primary method: Playwright API request in dedicated worker thread
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_fetch_leaderboard_pw, competition_id, self.auth_file)
                data = future.result(timeout=60)
        except Exception as e:
            logger.debug("Playwright leaderboard fetch failed: %s", e)

        # 2. Fallback method: urllib POST request with full headers & leaderboardMode
        if not data:
            payload = json.dumps({
                "competitionId": competition_id,
                "leaderboardMode": "LEADERBOARD_MODE_DEFAULT",
            }).encode("utf-8")
            headers = self._get_headers(is_json=True)
            headers["Accept"] = "application/json"
            headers["Referer"] = f"https://www.kaggle.com/competitions/{competition_id}/leaderboard"
            req = urllib.request.Request(
                GET_LEADERBOARD_URL,
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.error("Failed to fetch leaderboard: %s", e)
                return []

        rows = data.get("publicLeaderboard") or []
        teams_raw = data.get("teams") or []
        teams = {str(t.get("teamId")): t for t in teams_raw}

        normalized = []
        for i, row in enumerate(rows, start=1):
            team_id = str(row.get("teamId") or i)
            t_info = teams.get(team_id) or {}
            team_name = t_info.get("teamName") or f"Team_{team_id}"
            rank = int(row.get("rank") or i)
            score_val = row.get("displayScore")
            try:
                score = float(str(score_val).replace(",", "")) if score_val is not None else None
            except (ValueError, TypeError):
                score = None

            sub_id = row.get("submissionId")

            usernames: list[str] = []
            leader = t_info.get("teamUpInfo", {}).get("teamLeader", {})
            if leader.get("userName"):
                usernames.append(leader["userName"].lower())
            for m in t_info.get("teamMembers", []):
                if m.get("userName"):
                    usernames.append(m["userName"].lower())

            normalized.append({
                "team_id": team_id,
                "team_name": team_name,
                "rank": rank,
                "score": score,
                "best_submission_id": str(sub_id) if sub_id else None,
                "medal": row.get("medal"),
                "usernames": list(set(usernames)),
            })

        normalized.sort(key=lambda r: r["rank"])
        return normalized

    def list_episodes(self, submission_id: str | int, max_retries: int = 4) -> list[dict[str, Any]]:
        """Fetch list of matches for a player's submission ID with backoff."""
        sid_val = int(submission_id) if str(submission_id).isdigit() else str(submission_id)
        payload = json.dumps({"submissionId": sid_val}).encode("utf-8")

        for attempt in range(max_retries):
            req = urllib.request.Request(
                LIST_EPISODES_URL,
                data=payload,
                headers=self._get_headers(is_json=True),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("episodes") or []
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait_sec = 8 * (attempt + 1)
                    print(f" [Rate-limit 429: cooling down for {wait_sec}s ({attempt + 1}/{max_retries})...]", end="", flush=True)
                    import time
                    time.sleep(wait_sec)
                    print(" retrying...", flush=True)
                    continue
                logger.error("HTTP error %s listing episodes for submission %s", e.code, submission_id)
                return []
            except Exception as e:
                logger.error("Error listing episodes for submission %s: %s", submission_id, e)
                return []

        return []

    def download_replay_to_file(self, episode_id: str | int, output_file: Path, max_retries: int = 3) -> bool:
        """Stream a large replay JSON directly to disk, verifying complete JSON before finalizing."""
        url = f"https://www.kaggle.com/competitions/episodes/{episode_id}/replay.json"
        req = urllib.request.Request(url, headers=self._get_headers(is_json=False))
        output_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = output_file.with_suffix(f".{os.getpid()}_{time.time_ns()}.tmp")

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=90) as resp:
                    with open(tmp_file, "wb") as f_out:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f_out.write(chunk)

                if tmp_file.exists():
                    fsize = tmp_file.stat().st_size
                    if fsize > 500:
                        tail = b""
                        with open(tmp_file, "rb") as f_check:
                            f_check.seek(max(0, fsize - 4096))
                            tail = f_check.read().rstrip()

                        # Ensure file handle is closed before moving/replacing on Windows
                        if tail.endswith(b"}"):
                            for _ in range(5):
                                try:
                                    if output_file.exists():
                                        try:
                                            output_file.unlink()
                                        except Exception:
                                            pass
                                    tmp_file.replace(output_file)
                                    return True
                                except PermissionError:
                                    time.sleep(0.2)
                            if output_file.exists() and output_file.stat().st_size > 500:
                                return True

                    # Truncated or incomplete download
                    logger.warning(
                        "Replay %s download incomplete (%d bytes), retrying (%d/%d)...",
                        episode_id, fsize, attempt + 1, max_retries
                    )
                    if tmp_file.exists():
                        try:
                            tmp_file.unlink()
                        except Exception:
                            pass
                time.sleep(1)
            except Exception as e:
                logger.warning("Attempt %d failed downloading replay %s: %s", attempt + 1, episode_id, e)
                if tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except Exception:
                        pass
                time.sleep(1)

        return False


def determine_outcome(episode: dict, submission_id: str) -> str:
    """Classify match outcome as 'win', 'lose', 'draw', or 'unknown'."""
    agents = episode.get("agents") or []
    if not agents:
        return "unknown"

    target_agent = None
    sid_str = str(submission_id)
    for a in agents:
        if str(a.get("submissionId")) == sid_str:
            target_agent = a
            break

    if not target_agent:
        return "unknown"

    # Check explicit reward score
    reward = target_agent.get("reward")
    if reward is not None:
        other_rewards = [a.get("reward") for a in agents if a is not target_agent and a.get("reward") is not None]
        if other_rewards:
            max_other = max(other_rewards)
            if reward > max_other:
                return "win"
            if reward < max_other:
                return "lose"
            return "draw"

    # Check updatedScore delta (rating increase = win)
    init_score = target_agent.get("initialScore")
    upd_score = target_agent.get("updatedScore")
    if init_score is not None and upd_score is not None:
        if upd_score > init_score:
            return "win"
        if upd_score < init_score:
            return "lose"
        return "draw"

    return "unknown"
