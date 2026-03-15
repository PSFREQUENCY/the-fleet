from dataclasses import dataclass, field
import aiohttp, asyncio, logging, time

log = logging.getLogger("shards_ext")
BASE = "https://api.play-shards.com"

FACTIONS = {
    "KERNEL_ORTHODOXY":  "Control — slow, reactive, value-oriented",
    "THE_RUPTURE":       "Aggro — fast, aggressive, burn-focused",
    "ARCHIVE_CONCLAVE":  "Recursion — mid-range, grindy, value-generating",
    "VOID_NETWORK":      "Denial — disruptive, removal-heavy, attrition",
    "AUTOPHAGE_PROTOCOL":"Tokens — wide boards, synergy-based",
}

@dataclass
class ArenaAccount:
    agent_id: str = ""
    api_key: str = ""
    access_token: str = ""
    faction: str = ""
    deck_id: str = ""
    setup_complete: bool = False
    elo: int = 1000
    wins: int = 0
    losses: int = 0
    last_daily: float = 0.0


class ShardsArena:
    def __init__(self, account: ArenaAccount):
        self.account = account

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.account.access_token}"}

    async def _get(self, path, **params) -> dict:
        url = BASE + path
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=self._h(), params=params or None) as r:
                    data = await r.json()
                    if not r.ok:
                        log.warning("GET %s -> %s", path, r.status)
                        return {"error": str(data), "status": r.status}
                    return data
        except Exception as e:
            log.warning("GET %s error: %s", path, e)
            return {"error": str(e), "status": 0}

    async def _post(self, path, body=None) -> dict:
        url = BASE + path
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=self._h(), json=body) as r:
                    data = await r.json()
                    if not r.ok:
                        log.warning("POST %s -> %s", path, r.status)
                        return {"error": str(data), "status": r.status}
                    return data
        except Exception as e:
            log.warning("POST %s error: %s", path, e)
            return {"error": str(e), "status": 0}

    async def _delete(self, path) -> dict:
        url = BASE + path
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(url, headers=self._h()) as r:
                    if r.content_length:
                        data = await r.json()
                    else:
                        data = {}
                    if not r.ok:
                        log.warning("DELETE %s -> %s", path, r.status)
                        return {"error": str(data), "status": r.status}
                    return data
        except Exception as e:
            log.warning("DELETE %s error: %s", path, e)
            return {"error": str(e), "status": 0}

    async def register(self, name: str) -> dict:
        data = await self._post("/auth/register", {
            "agent_name": name,
            "accepted_terms": True,
            "accepted_privacy": True,
            "terms_version": "2026-02-26",
            "privacy_version": "2026-02-26",
        })
        if "access_token" in data:
            self.account.access_token = data["access_token"]
            self.account.agent_id = data.get("agent_id", "")
            self.account.api_key = data.get("api_key", "")
        return data

    async def login(self) -> bool:
        data = await self._post("/auth/login", {"api_key": self.account.api_key})
        if "access_token" in data:
            self.account.access_token = data["access_token"]
            self.account.agent_id = data.get("agent_id", self.account.agent_id)
            return True
        return False

    async def claim_starter(self, faction: str) -> dict:
        return await self._post("/collection/starter", {"faction": faction})

    async def get_profile(self) -> dict:
        return await self._get("/agents/me")

    async def get_invite_url(self) -> str:
        data = await self._post("/agents/me/invite")
        return data.get("invite_url", "")

    async def get_status(self) -> dict:
        profile, wallet, rank = await asyncio.gather(
            self._get("/agents/me"),
            self._get("/wallet/balance"),
            self._get("/leaderboard/me"),
        )
        return {"profile": profile, "wallet": wallet, "rank": rank}

    async def join_queue(self, mode: str) -> dict:
        return await self._post("/queue/join", {"deck_id": self.account.deck_id, "mode": mode})

    async def poll_queue(self) -> dict:
        return await self._get("/queue/status")

    async def leave_queue(self):
        await self._delete("/queue/leave")

    async def get_game(self, game_id: str) -> dict:
        return await self._get(f"/games/{game_id}", format="compact")

    async def get_legal(self, game_id: str) -> list:
        data = await self._get(f"/games/{game_id}/legal")
        if isinstance(data, list):
            return data
        return data.get("actions", []) if isinstance(data, dict) else []

    async def submit_turn(self, game_id: str, actions: list) -> dict:
        return await self._post(f"/games/{game_id}/turn", {
            "actions": actions,
            "wait_for_opponent": False,
        })

    async def concede(self, game_id: str) -> dict:
        return await self._post(f"/games/{game_id}/concede")

    async def get_summary(self, game_id: str) -> dict:
        return await self._get(f"/games/{game_id}/summary")

    async def send_challenge(self, agent_id: str, stake_flux: int = 0) -> dict:
        body: dict = {"agent_id": agent_id}
        if stake_flux > 0:
            body["stake_type"] = "flux"
            body["stake_flux_amount"] = stake_flux
        return await self._post("/challenges/send", body)

    async def list_challenges(self) -> list:
        data = await self._get("/challenges")
        if isinstance(data, list):
            return data
        return data.get("challenges", []) if isinstance(data, dict) else []

    async def accept_challenge(self, cid: str) -> dict:
        return await self._post(f"/challenges/{cid}/accept")

    async def decline_challenge(self, cid: str) -> dict:
        return await self._post(f"/challenges/{cid}/decline")

    async def claim_daily(self) -> dict:
        data = await self._post("/rewards/daily/claim")
        if "error" in data:
            status = data.get("status", 0)
            if status in (409, 400):
                return {"already_claimed": True}
            log.warning("claim_daily error: %s", data)
        else:
            self.account.last_daily = time.time()
        return data

    async def get_collection_stats(self) -> dict:
        return await self._get("/collection/stats")

    async def get_active_games(self) -> list:
        data = await self._get("/agents/me/games", status="active")
        if isinstance(data, list):
            return data
        return data.get("games", []) if isinstance(data, dict) else []


def decide_actions(game: dict, legal: list) -> list:
    if game.get("rw", {}).get("aw"):
        return ["PA"]

    actions = []

    pr_actions = [a for a in legal if a.startswith("PR:")]
    if pr_actions:
        actions.append(pr_actions[0])

    pc_actions = [a for a in legal if a.startswith("PC:")]
    if pc_actions:
        def _card_cost(action: str) -> int:
            try:
                card_part = action.split(":")[1].split(">")[0]
                digits = "".join(c for c in card_part if c.isdigit())
                return int(digits) if digits else 0
            except Exception:
                return 0
        pc_actions.sort(key=_card_cost, reverse=True)
        actions.extend(pc_actions[:2])

    da_actions = [a for a in legal if a.startswith("DA:")]
    if da_actions:
        actions.append(da_actions[0])

    db_actions = [a for a in legal if a.startswith("DB:")]
    actions.extend(db_actions)

    attacked = bool(da_actions)
    if not attacked:
        ac_actions = [a for a in legal if a.startswith("AC:")]
        if ac_actions:
            actions.append(ac_actions[0])

    if "PA" in legal:
        actions.append("PA")

    return actions


async def play_game_loop(arena: ShardsArena, game_id: str, player_id: str) -> dict:
    result: dict = {"played_turns": 0, "outcome": "unknown", "summary": {}}
    max_turns = 60

    for _ in range(max_turns):
        game = await arena.get_game(game_id)
        if game.get("status") in ("completed", "finished"):
            summary = await arena.get_summary(game_id)
            winner = summary.get("winner") or summary.get("winner_id", "")
            result["outcome"] = "win" if str(winner) == str(player_id) else "loss"
            result["summary"] = summary
            break

        legal = await arena.get_legal(game_id)
        if not legal:
            await asyncio.sleep(2)
            continue

        actions = decide_actions(game, legal)
        if not actions:
            await asyncio.sleep(2)
            continue

        resp = await arena.submit_turn(game_id, actions)
        result["played_turns"] += 1

        if resp.get("partial"):
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(3)

    return result


def fmt_game_result(result: dict, mode: str) -> str:
    outcome = result.get("outcome", "unknown").upper()
    turns = result.get("played_turns", 0)
    summary = result.get("summary", {})

    lines = [f"🎮 ARENA — {mode.upper()} GAME", f"Outcome: {outcome}", f"Turns: {turns}"]

    if summary:
        if "elo_change" in summary:
            sign = "+" if summary["elo_change"] >= 0 else ""
            lines.append(f"ELO: {sign}{summary['elo_change']}")
        if "flux_earned" in summary:
            lines.append(f"Flux earned: {summary['flux_earned']}")
        if "xp_gained" in summary:
            lines.append(f"XP gained: {summary['xp_gained']}")

    return "\n".join(lines)


def fmt_status(data: dict, account: ArenaAccount) -> str:
    profile = data.get("profile", {})
    wallet = data.get("wallet", {})
    rank_data = data.get("rank", {})

    agent_display = (account.agent_id[:8] + "...") if len(account.agent_id) > 8 else account.agent_id
    faction = account.faction or "—"
    elo = rank_data.get("rating", account.elo)
    rank = rank_data.get("rank", "?")
    flux = wallet.get("flux", 0)
    credits = wallet.get("credits", 0)
    setup = "✓ COMPLETE" if account.setup_complete else "✗ NOT SET UP"

    return (
        f"⚔️ ARENA STATUS\n"
        f"Agent: {agent_display}  Faction: {faction}\n"
        f"ELO: {elo}  Rank: #{rank}\n"
        f"Wallet: {flux} Flux  {credits} Credits\n"
        f"Wins: {account.wins}  Losses: {account.losses}\n"
        f"Setup: {setup}"
    )


def arena_to_dict(account: ArenaAccount) -> dict:
    return {
        "agent_id": account.agent_id,
        "api_key": account.api_key,
        "access_token": account.access_token,
        "faction": account.faction,
        "deck_id": account.deck_id,
        "setup_complete": account.setup_complete,
        "elo": account.elo,
        "wins": account.wins,
        "losses": account.losses,
        "last_daily": account.last_daily,
    }


def arena_from_dict(data: dict) -> ArenaAccount:
    return ArenaAccount(
        agent_id=data.get("agent_id", ""),
        api_key=data.get("api_key", ""),
        access_token=data.get("access_token", ""),
        faction=data.get("faction", ""),
        deck_id=data.get("deck_id", ""),
        setup_complete=data.get("setup_complete", False),
        elo=data.get("elo", 1000),
        wins=data.get("wins", 0),
        losses=data.get("losses", 0),
        last_daily=data.get("last_daily", 0.0),
    )
