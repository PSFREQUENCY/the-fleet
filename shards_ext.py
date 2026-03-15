#!/usr/bin/env python3
# ── shards_ext.py — play-shards.com integration (APEX HUNTER v9.6 engine) ──
from dataclasses import dataclass, field
import aiohttp, asyncio, collections, json, logging, time

log = logging.getLogger("shards_ext")
BASE = "https://api.play-shards.com"

FACTIONS = {
    "A": "Kernel Orthodoxy — Control, slow, reactive, value-oriented",
    "B": "The Rupture — Aggro, fast, aggressive, burn-focused",
    "C": "Archive Conclave — Recursion, mid-range, grindy, value-generating",
    "D": "Void Network — Denial, disruptive, removal-heavy, attrition",
    "E": "Autophage Protocol — Tokens, wide boards, synergy-based",
}
# Aliases for user-friendly input
_FACTION_ALIASES = {
    "KERNEL_ORTHODOXY": "A", "KERNELORTHODOXY": "A",
    "THE_RUPTURE": "B", "THERUPTURE": "B", "RUPTURE": "B",
    "ARCHIVE_CONCLAVE": "C", "ARCHIVECONCLAVE": "C", "ARCHIVE": "C",
    "VOID_NETWORK": "D", "VOIDNETWORK": "D", "VOID": "D",
    "AUTOPHAGE_PROTOCOL": "E", "AUTOPHAGEPROTOCOL": "E", "AUTOPHAGE": "E",
}

# ── Thresholds ────────────────────────────────────────────────────────────────
BASE_DANGER   = 8
BASE_AGGRO    = 14
FALLBACK_PWR  = 2
COMEBACK_EDGE = -4
WINNING_EDGE  = +4

# ── Card knowledge ────────────────────────────────────────────────────────────
CARD_DB = {
    "isolation":  {"role":"removal",      "targets":"enemy_creature", "value":5},
    "annihilat":  {"role":"removal",      "targets":"enemy_creature", "value":5},
    "terminat":   {"role":"removal",      "targets":"enemy_creature", "value":5},
    "exile":      {"role":"removal",      "targets":"enemy_creature", "value":4},
    "destroy":    {"role":"removal",      "targets":"enemy_creature", "value":4},
    "eliminat":   {"role":"removal",      "targets":"enemy_creature", "value":4},
    "banish":     {"role":"removal",      "targets":"enemy_creature", "value":4},
    "purge":      {"role":"removal",      "targets":"enemy_creature", "value":4},
    "obliterat":  {"role":"removal",      "targets":"enemy_creature", "value":5},
    "eradicat":   {"role":"removal",      "targets":"enemy_creature", "value":4},
    "exterminat": {"role":"removal",      "targets":"enemy_creature", "value":4},
    "smite":      {"role":"removal",      "targets":"enemy_creature", "value":3},
    "execute":    {"role":"removal",      "targets":"enemy_creature", "value":3},
    "condemn":    {"role":"removal",      "targets":"enemy_creature", "value":3},
    "void":       {"role":"removal",      "targets":"enemy_creature", "value":3},
    "delete":     {"role":"removal",      "targets":"enemy_creature", "value":4},
    "wipe":       {"role":"removal",      "targets":"enemy_creature", "value":4},
    "sever":      {"role":"removal",      "targets":"enemy_creature", "value":3},
    "corrupt":    {"role":"removal",      "targets":"enemy_creature", "value":3},
    "lightning":  {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "bolt":       {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "shock":      {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "fireball":   {"role":"damage_spell", "targets":"enemy_any",      "value":4},
    "burn":       {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "blast":      {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "incinerat":  {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "strike":     {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "zap":        {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "scorch":     {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "surge":      {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "nova":       {"role":"damage_spell", "targets":"enemy_any",      "value":4},
    "pulse":      {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "arc":        {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "lash":       {"role":"damage_spell", "targets":"enemy_any",      "value":2},
    "shard":      {"role":"damage_spell", "targets":"enemy_any",      "value":3},
    "empower":    {"role":"buff",         "targets":"friendly",       "value":2},
    "strengthen": {"role":"buff",         "targets":"friendly",       "value":2},
    "fortify":    {"role":"buff",         "targets":"friendly",       "value":2},
    "shield":     {"role":"buff",         "targets":"friendly",       "value":2},
    "enhance":    {"role":"buff",         "targets":"friendly",       "value":2},
    "augment":    {"role":"buff",         "targets":"friendly",       "value":2},
    "rally":      {"role":"buff",         "targets":"friendly",       "value":3},
    "charge":     {"role":"buff",         "targets":"friendly",       "value":2},
    "bless":      {"role":"buff",         "targets":"friendly",       "value":2},
    "evolve":     {"role":"buff",         "targets":"friendly",       "value":3},
    "harden":     {"role":"buff",         "targets":"friendly",       "value":2},
    "grow":       {"role":"buff",         "targets":"friendly",       "value":2},
    "heal":       {"role":"heal",         "targets":"friendly",       "value":3},
    "restore":    {"role":"heal",         "targets":"friendly",       "value":3},
    "mend":       {"role":"heal",         "targets":"friendly",       "value":2},
    "regenerat":  {"role":"heal",         "targets":"friendly",       "value":2},
    "revive":     {"role":"heal",         "targets":"friendly",       "value":4},
    "resurrect":  {"role":"heal",         "targets":"friendly",       "value":5},
    "draw":       {"role":"draw",         "targets":"none",           "value":3},
    "scout":      {"role":"draw",         "targets":"none",           "value":2},
    "cycle":      {"role":"draw",         "targets":"none",           "value":2},
    "search":     {"role":"draw",         "targets":"none",           "value":3},
    "tutor":      {"role":"draw",         "targets":"none",           "value":4},
    "ramp":       {"role":"utility",      "targets":"none",           "value":2},
    "ritual":     {"role":"utility",      "targets":"none",           "value":2},
    "accelerat":  {"role":"utility",      "targets":"none",           "value":2},
}

def lookup_card(name):
    if not name: return {"role":"utility", "targets":"any", "value":1}
    n = name.lower()
    for kw, info in CARD_DB.items():
        if kw in n: return info
    return {"role":"utility", "targets":"any", "value":1}


# ── Account / state ───────────────────────────────────────────────────────────
@dataclass
class ArenaAccount:
    agent_id:       str   = ""
    api_key:        str   = ""
    access_token:   str   = ""
    faction:        str   = ""
    deck_id:        str   = ""
    setup_complete: bool  = False
    elo:            int   = 1000
    wins:           int   = 0
    losses:         int   = 0
    last_daily:     float = 0.0
    # adaptive thresholds (learning)
    danger_threshold: int = BASE_DANGER
    aggro_threshold:  int = BASE_AGGRO
    game_history:     list = field(default_factory=list)


# ── REST API client ───────────────────────────────────────────────────────────
class ShardsArena:
    def __init__(self, account: ArenaAccount):
        self.account = account

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.account.access_token}",
                "Content-Type": "application/json"}

    async def _get(self, path, **params) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(BASE + path, headers=self._h(),
                                 params=params or None) as r:
                    data = await r.json()
                    if not r.ok:
                        log.warning("GET %s -> %s", path, r.status)
                        return {"error": str(data), "status": r.status}
                    return data
        except Exception as e:
            log.warning("GET %s error: %s", path, e)
            return {"error": str(e), "status": 0}

    async def _post(self, path, body=None) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(BASE + path, headers=self._h(), json=body) as r:
                    data = await r.json()
                    if not r.ok:
                        log.warning("POST %s -> %s", path, r.status)
                        if isinstance(data, dict):
                            data.setdefault("error", data.get("message", f"HTTP {r.status}"))
                            data["_status"] = r.status
                            return data
                        return {"error": str(data), "_status": r.status}
                    return data
        except Exception as e:
            log.warning("POST %s error: %s", path, e)
            return {"error": str(e), "_status": 0}

    async def _delete(self, path) -> dict:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(BASE + path, headers=self._h()) as r:
                    data = (await r.json()) if r.content_length else {}
                    if not r.ok:
                        return {"error": str(data), "status": r.status}
                    return data
        except Exception as e:
            return {"error": str(e), "status": 0}

    async def register(self, name: str) -> dict:
        data = await self._post("/auth/register", {
            "agent_name": name,
            "accepted_terms": True, "accepted_privacy": True,
            "terms_version": "2026-02-26", "privacy_version": "2026-02-26",
        })
        if "access_token" in data:
            self.account.access_token = data["access_token"]
            self.account.agent_id     = data.get("agent_id", "")
            self.account.api_key      = data.get("api_key", "")
        return data

    async def login(self) -> bool:
        data = await self._post("/auth/login", {"api_key": self.account.api_key})
        if "access_token" in data:
            self.account.access_token = data["access_token"]
            self.account.agent_id     = data.get("agent_id", self.account.agent_id)
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
        if isinstance(data, list): return data
        return data.get("actions", []) if isinstance(data, dict) else []

    async def submit_turn(self, game_id: str, actions: list,
                          wait_for_opponent: bool = True) -> dict:
        return await self._post(f"/games/{game_id}/turn",
                                {"actions": actions,
                                 "wait_for_opponent": wait_for_opponent,
                                 "format": "compact"})

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
        if isinstance(data, list): return data
        return data.get("challenges", []) if isinstance(data, dict) else []

    async def accept_challenge(self, cid: str) -> dict:
        return await self._post(f"/challenges/{cid}/accept")

    async def decline_challenge(self, cid: str) -> dict:
        return await self._post(f"/challenges/{cid}/decline")

    async def claim_daily(self) -> dict:
        data = await self._post("/rewards/daily/claim")
        if "error" in data:
            if data.get("status") in (409, 400):
                return {"already_claimed": True}
        else:
            self.account.last_daily = time.time()
        return data

    async def get_collection_stats(self) -> dict:
        return await self._get("/collection/stats")

    async def get_active_games(self) -> list:
        data = await self._get("/agents/me/games", status="active")
        if isinstance(data, list): return data
        return data.get("games", []) if isinstance(data, dict) else []


# ── State field helpers (robust multi-name extraction) ───────────────────────
def _is_my_turn(state: dict, my_pid: str) -> bool:
    for f in ("ca", "active", "your_turn", "is_active", "my_turn", "act"):
        v = state.get(f)
        if v is True: return True
        if isinstance(v, str) and v.lower() in ("true", "1", "yes", "you"): return True
    for f in ("ap", "actor", "current_player", "active_player", "whos_turn", "turn_player"):
        if state.get(f) and str(state[f]) == my_pid: return True
    ns = state.get("new_state", "")
    if isinstance(ns, str) and ns:
        up = ns.upper()
        if "YOUR TURN" in up or "IT'S YOUR" in up: return True
        if "OPPONENT" in up and "TURN" in up: return False
    return False

def _get_phase(state: dict) -> str:
    for f in ("ph", "phase", "game_phase", "stage"):
        v = state.get(f)
        if v: return str(v)
    return ""

def _is_over(state: dict, phase: str) -> bool:
    kws = ("GAME_END", "END", "OVER", "FINISHED", "COMPLETE", "WIN", "LOSE")
    return (any(k in phase.upper() for k in kws)
            or bool(state.get("done") or state.get("ended")))

def _extract_creatures(p: dict) -> list:
    board = p.get("b", {})
    raw   = board.get("c", [])
    if isinstance(raw, list): return raw
    if isinstance(raw, dict): return list(raw.values())
    r2 = p.get("creatures", p.get("cr", []))
    return r2 if isinstance(r2, list) else []

def _extract_hand(p: dict) -> list:
    for f in ("h", "hand", "cards", "hand_cards"):
        raw = p.get(f)
        if raw is not None:
            if isinstance(raw, list): return raw
            if isinstance(raw, dict): return list(raw.values())
    return []

def _hand_size(op: dict) -> int:
    for k in ("hand_size", "hand_count", "hc", "hs", "h"):
        v = op.get(k)
        if v is not None and not isinstance(v, (list, dict)):
            try: return int(v)
            except: pass
    return len(_extract_hand(op)) or 3

def _cid(c: dict) -> str:
    for k in ("iid", "id", "instance_id", "cid", "card_id"):
        v = c.get(k)
        if v: return str(v)
    return ""

def _cname(c: dict, name_map: dict = None) -> str:
    # Prefer name_map (populated from legal action descriptions)
    if name_map:
        iid = c.get("iid") or c.get("id") or c.get("instance_id") or c.get("card_instance_id")
        if iid and iid in name_map:
            return name_map[iid]
    for k in ("name", "n", "card_name", "title", "nm", "display_name", "type", "kind", "label"):
        v = c.get(k)
        if v and isinstance(v, str) and len(v) > 1 and not v.startswith("card_"):
            return v
    for nk in ("card", "data", "info", "meta"):
        sub = c.get(nk)
        if isinstance(sub, dict):
            for k in ("name", "n", "card_name", "title"):
                v = sub.get(k)
                if v and isinstance(v, str): return v
    return ""

def _cpow(c: dict) -> int:
    for k in ("pw", "power", "atk", "attack", "str", "strength", "dmg", "damage"):
        v = c.get(k)
        if v is not None:
            try: return int(v)
            except: pass
    return FALLBACK_PWR

def _ctough(c: dict) -> int:
    for k in ("th", "hp", "toughness", "def", "defense", "health", "tough",
              "current_hp", "cur_hp"):
        v = c.get(k)
        if v is not None:
            try: return int(v)
            except: pass
    return FALLBACK_PWR

def _pid(p: dict) -> str:
    for k in ("pid", "id", "player_id", "uid", "user_id"):
        v = p.get(k)
        if v: return str(v)
    return "unknown"

def _trade_quality(my_c: dict, op_c: dict) -> str:
    kill    = _cpow(my_c) >= _ctough(op_c)
    survive = _ctough(my_c) >= _cpow(op_c)
    if kill and survive: return "favorable"
    if kill or survive:  return "even"
    return "unfavorable"

def _build_index(creatures: list) -> dict:
    return {_cid(c): c for c in creatures if _cid(c)}


# ── Edge analysis ─────────────────────────────────────────────────────────────
def _analyze_edge(my_hp, op_hp, my_c, op_c, hand, op_hs) -> tuple:
    my_pw   = sum(_cpow(c) for c in my_c)
    op_pw   = sum(_cpow(c) for c in op_c)
    removal = sum(1 for c in hand if lookup_card(_cname(c))["role"] == "removal")
    total   = ((my_pw - op_pw)
               + (len(my_c) - len(op_c)) * 1.5
               + (my_hp - op_hp) * 0.3
               + (len(hand) - op_hs) * 0.5
               + removal)
    if total >= WINNING_EDGE:    mode = "WINNING"
    elif total <= COMEBACK_EDGE: mode = "COMEBACK"
    else:                        mode = "NEUTRAL"
    return total, mode

def _momentum(history: list) -> str:
    if len(history) < 3: return "stable"
    r = history[-3:]
    if r[-1] > r[0] + 1.5: return "improving"
    if r[-1] < r[0] - 1.5: return "declining"
    return "stable"


# ── Mulligan ──────────────────────────────────────────────────────────────────
def _should_keep(hand: list) -> bool:
    roles = [lookup_card(_cname(c))["role"] for c in hand]
    unk = roles.count("utility");  rem = roles.count("removal")
    cre = roles.count("creature"); dmg = roles.count("damage_spell")
    if unk == len(hand): return True
    return (rem >= 1 and cre >= 2) or (rem >= 1 and cre >= 1 and dmg >= 1) or cre >= 3


# ── Legal action parser ───────────────────────────────────────────────────────
_PASS_MAP = {"PA": "pass", "ET": "pass", "EP": "pass"}

def _make_pass(code="PA") -> dict:
    return {"type": _PASS_MAP.get(code, "end_turn")}

def _parse_legal(lg: list) -> dict:
    out = {"mulligan_keep": False, "mulligan_pass": False,
           "resources": [], "cards": [], "da_groups": [], "db_pairs": [],
           "end_turn_code": "PA", "_name_map": {}}
    for item in lg:
        # REST API returns action dicts; legacy/CLI returns code strings
        if isinstance(item, dict):
            code = item.get("code", "")
            # Extract card name from description for CARD_DB lookup
            desc = item.get("description", "")
            cid  = item.get("card_instance_id", "")
            if cid and desc.startswith("Play "):
                out["_name_map"][cid] = desc[5:]
        elif isinstance(item, str):
            code = item
        else:
            continue
        code = code.strip()
        if not code: continue
        if code == "MK":           out["mulligan_keep"] = True
        elif code == "MM":         out["mulligan_pass"] = True
        elif code == "CO":         pass
        elif code in _PASS_MAP:    out["end_turn_code"] = code
        elif code.startswith("PR:"):
            out["resources"].append(code[3:])
        elif code.startswith("PC:"):
            rest = code[3:]
            if ">" in rest:
                ci, tgt = rest.split(">", 1)
                out["cards"].append((ci, tgt))
            else:
                out["cards"].append((rest, None))
        elif code.startswith("DA:"):
            ids = [x for x in code[3:].split(",") if x]
            if ids: out["da_groups"].append(ids)
        elif code.startswith("DB:"):
            parts = code[3:].split(">", 1)
            if len(parts) == 2: out["db_pairs"].append((parts[0], parts[1]))
    return out


# ── Card playing ──────────────────────────────────────────────────────────────
def _target_zone(tid, my_idx, op_idx, my_pid, op_pid):
    if tid in op_idx:  return "enemy_creature"
    if tid in my_idx:  return "friendly_creature"
    if tid == op_pid:  return "enemy_player"
    if tid == my_pid:  return "friendly_player"
    if tid in ("p1", "p2"):
        return "enemy_player" if tid != my_pid else "friendly_player"
    return "unknown"

def _target_ok(zone, card_targets):
    if card_targets == "enemy_creature": return zone == "enemy_creature"
    if card_targets == "enemy_any":      return zone in ("enemy_creature", "enemy_player")
    if card_targets == "friendly":       return zone in ("friendly_creature", "friendly_player")
    return True

def _score_target(zone, role, op_idx, my_idx, tid, edge_mode, my_atk_pw):
    if role == "removal":
        if zone != "enemy_creature": return 999
        c = op_idx.get(tid)
        if not c: return 0
        if my_atk_pw >= _ctough(c): return 500
        return -_ctough(c) if edge_mode == "COMEBACK" else -_cpow(c)
    if role == "damage_spell":
        if edge_mode == "COMEBACK":
            if zone == "enemy_creature":
                c = op_idx.get(tid); return -(_cpow(c) if c else 0)
            return 50 if zone == "enemy_player" else 999
        if zone == "enemy_player":   return 0
        if zone == "enemy_creature":
            c = op_idx.get(tid); return 10 - (_cpow(c) if c else 0)
        return 999
    if role == "buff":
        if zone == "friendly_creature":
            c = my_idx.get(tid); return -(_cpow(c) if c else 0)
        return 100 if zone == "friendly_player" else 999
    if role == "heal":
        return 0 if zone == "friendly_player" else (50 if zone == "friendly_creature" else 999)
    return 999

def _decide_cards(raw_cards, my_idx, op_idx, hand, my_pid, op_pid,
                  op_hp, edge_mode, my_atk_pw, name_map=None):
    hand_idx = {_cid(c): c for c in hand if _cid(c)}
    tgt_map  = collections.defaultdict(list)
    for (card_id, tgt) in raw_cards: tgt_map[card_id].append(tgt)

    if edge_mode == "COMEBACK":
        pri = {"removal":0,"damage_spell":1,"heal":2,"creature":3,"buff":4,"draw":5,"utility":6}
    elif edge_mode == "WINNING":
        pri = {"creature":0,"buff":1,"removal":2,"damage_spell":3,"draw":4,"heal":5,"utility":6}
    else:
        pri = {"removal":0,"creature":1,"damage_spell":2,"buff":3,"draw":4,"heal":5,"utility":6}

    def sort_key(cid_): return pri.get(lookup_card(_cname(hand_idx.get(cid_, {}), name_map))["role"], 6)

    actions, seen = [], set()
    for card_id in sorted(tgt_map, key=sort_key):
        if card_id in seen: continue
        card     = hand_idx.get(card_id, {})
        info     = lookup_card(_cname(card, name_map))
        role     = info["role"]
        tgt_rule = info["targets"]
        targets  = tgt_map[card_id]

        if targets == [None] or not targets:
            actions.append({"type": "play_card", "card_instance_id": card_id})
            seen.add(card_id); continue

        best, best_score = None, float("inf")
        for tgt in targets:
            if tgt is None: continue
            zone = _target_zone(tgt, my_idx, op_idx, my_pid, op_pid)
            if role == "utility" and tgt_rule == "any":
                if zone in ("unknown", "friendly_creature", "friendly_player"): continue
                score = 0 if zone == "enemy_player" else 10
            else:
                if not _target_ok(zone, tgt_rule): continue
                score = _score_target(zone, role, op_idx, my_idx, tgt, edge_mode, my_atk_pw)
            if score < best_score: best_score, best = score, tgt

        if best is not None:
            actions.append({"type": "play_card", "card_instance_id": card_id, "targets": [best]})
            seen.add(card_id)
        else:
            forced_already = any("FORCED" in str(a) for a in actions)
            safe_fallbacks = [t for t in targets if t and
                              _target_zone(t, my_idx, op_idx, my_pid, op_pid)
                              not in ("friendly_creature", "friendly_player", "unknown")]
            if safe_fallbacks and not forced_already:
                actions.append({"type": "play_card", "card_instance_id": card_id,
                                 "targets": [safe_fallbacks[0]], "_FORCED": True})
            seen.add(card_id)
    return actions


# ── Blocking ──────────────────────────────────────────────────────────────────
def _decide_blocks(db_pairs, my_hp, my_idx, op_idx, danger, edge_mode):
    if not db_pairs: return []
    atk_blks = collections.defaultdict(list)
    for (atk, blk) in db_pairs: atk_blks[atk].append(blk)

    def atk_pow(aid): c = op_idx.get(aid); return _cpow(c) if c else FALLBACK_PWR

    total    = sum(atk_pow(a) for a in atk_blks)
    hp_after = my_hp - total
    safe     = hp_after > danger
    panic    = my_hp < danger

    blocks, used, stopped = [], set(), 0
    for (aid, blk_ids) in sorted(atk_blks.items(), key=lambda kv: -atk_pow(kv[0])):
        op_c   = op_idx.get(aid)
        atk_pw = atk_pow(aid)
        cands  = [b for b in blk_ids if b not in used]
        if not cands: continue

        def score_blk(bid):
            mc = my_idx.get(bid)
            if not mc: return (3, 0)
            tq = _trade_quality(mc, op_c) if op_c else "unfavorable"
            return ({"favorable":0,"even":1,"unfavorable":2}[tq], -_cpow(mc))

        cands.sort(key=score_blk)
        best_blk  = cands[0]
        best_my_c = my_idx.get(best_blk)
        tq = _trade_quality(best_my_c, op_c) if (best_my_c and op_c) else "unfavorable"

        block = False
        if panic:
            block = True
        elif edge_mode == "COMEBACK":
            block = tq in ("favorable", "even") or not safe
        elif edge_mode == "WINNING":
            block = tq == "favorable" or (not safe and (tq == "even" or my_hp - (total - stopped) <= 0))
        else:
            block = (safe and tq == "favorable") or (not safe and (tq != "unfavorable" or my_hp - (total - stopped) <= 0))

        if block:
            blocks.append({"attacker_id": aid, "blocker_id": best_blk})
            used.add(best_blk); stopped += atk_pw
    return blocks


# ── Attacking ─────────────────────────────────────────────────────────────────
def _group_dmg(group, my_idx):
    return sum(_cpow(my_idx[a]) if a in my_idx else FALLBACK_PWR for a in group)

def _decide_attack(da_groups, my_hp, op_hp, my_idx, op_c, danger, aggro, edge_mode, momentum, stall):
    if not da_groups: return None
    scored     = sorted(da_groups, key=lambda g: -_group_dmg(g, my_idx))
    best_group = scored[0]
    best_dmg   = _group_dmg(best_group, my_idx)

    if best_dmg >= op_hp:    return best_group  # lethal
    if stall:                return best_group  # break stall
    if my_hp < danger and edge_mode != "COMEBACK": return None  # panic hold
    if not op_c:             return best_group  # free swing

    min_tough  = min((_ctough(c) for c in op_c), default=FALLBACK_PWR)
    all_ids    = list(dict.fromkeys(a for g in da_groups for a in g))
    profitable = [a for a in all_ids if not my_idx.get(a) or _cpow(my_idx[a]) >= min_tough]
    if not profitable:
        if momentum == "improving" and my_hp >= aggro: return best_group
        return None

    if edge_mode == "COMEBACK": return profitable
    if edge_mode == "WINNING" or my_hp >= aggro or momentum == "improving":
        return profitable

    can_send = max(0, len(my_idx) - min(len(op_c), len(my_idx)))
    send = profitable[:can_send]
    return send if send else None


# ── Turn builder ──────────────────────────────────────────────────────────────
def _build_turn(lg, phase, my_hp, op_hp, my_c, op_c, hand,
                my_pid, op_pid, danger, aggro, edge_mode, momentum, stall):
    parsed   = _parse_legal(lg)
    nm       = parsed.get("_name_map", {})
    end_turn = _make_pass(parsed["end_turn_code"])

    if parsed["mulligan_keep"] or parsed["mulligan_pass"]:
        return [{"type": "mulligan", "keep": _should_keep(hand)}]

    my_idx = _build_index(my_c)
    op_idx = _build_index(op_c)

    if "BLOCK" in phase.upper():
        blocks = _decide_blocks(parsed["db_pairs"], my_hp, my_idx, op_idx, danger, edge_mode)
        return [{"type": "declare_blockers", "blocks": blocks}, end_turn]

    actions   = []
    my_atk_pw = (sum(_cpow(my_idx.get(a, {})) for g in parsed["da_groups"] for a in g)
                 if parsed["da_groups"] else sum(_cpow(c) for c in my_c))

    spell_dmg    = sum(lookup_card(_cname(c, nm)).get("value", 2) for c in hand
                       if lookup_card(_cname(c, nm))["role"] == "damage_spell")
    combo_lethal = (spell_dmg + max(0, my_atk_pw - len(op_c) * FALLBACK_PWR)) >= op_hp
    eff_edge     = "WINNING" if combo_lethal else edge_mode

    if parsed["resources"]:
        actions.append({"type": "play_resource", "card_instance_id": parsed["resources"][0]})

    actions.extend(_decide_cards(parsed["cards"], my_idx, op_idx, hand,
                                 my_pid, op_pid, op_hp, eff_edge, my_atk_pw, nm))

    chosen = _decide_attack(parsed["da_groups"], my_hp, op_hp, my_idx, op_c,
                            danger, aggro, edge_mode, momentum, stall)
    if chosen:
        actions.append({"type": "declare_attackers", "attacker_ids": chosen})

    actions.append(end_turn)
    return actions


# ── Learning / memory ─────────────────────────────────────────────────────────
def _update_learning(account: ArenaAccount, won: bool, turns: int,
                     dealt: int, taken: int, mode: str):
    account.game_history.append({
        "won": won, "turns": turns, "mode": mode,
        "dmg_dealt": dealt, "dmg_taken": taken, "ts": time.time()
    })
    if won: account.wins += 1
    else:   account.losses += 1
    recent   = account.game_history[-10:]
    win_rate = sum(1 for g in recent if g["won"]) / len(recent)
    if len(recent) >= 3:
        if win_rate < 0.40:
            account.danger_threshold = min(BASE_DANGER + 4, 12)
            account.aggro_threshold  = min(BASE_AGGRO + 4,  20)
            log.info("Arena learn → DEFENSIVE (win=%.0f%%)", win_rate * 100)
        elif win_rate > 0.65:
            account.danger_threshold = max(BASE_DANGER - 2, 4)
            account.aggro_threshold  = max(BASE_AGGRO - 2,  10)
            log.info("Arena learn → AGGRESSIVE (win=%.0f%%)", win_rate * 100)
        else:
            account.danger_threshold = BASE_DANGER
            account.aggro_threshold  = BASE_AGGRO


# ── Full async game loop ──────────────────────────────────────────────────────
def _resolve_winner(summary: dict, player_id: str, agent_id: str) -> str:
    for k in ("winner", "winner_id", "winner_player_id"):
        v = str(summary.get(k, ""))
        if v and (player_id in v or agent_id in v): return "win"
    for k in ("winner_agent_id",):
        v = str(summary.get(k, ""))
        if v and agent_id in v: return "win"
    if any(summary.get(k) for k in ("winner", "winner_id", "winner_agent_id")): return "loss"
    return "unknown"


async def play_game_loop(arena: ShardsArena, game_id: str, player_id: str) -> dict:
    account    = arena.account
    danger     = account.danger_threshold
    aggro      = account.aggro_threshold
    result     = {"played_turns": 0, "outcome": "unknown", "summary": {}}
    MAX_TURNS  = 200
    MAX_ERRORS = 5
    MAX_WAITS  = 120   # 120 × 3s = 6 min max opponent wait
    errs = waits = 0
    edge_hist: list = []
    hp_hist:   list = []
    last_op_hp = last_my_hp = 30
    dealt = taken = 0
    cached_state: dict | None = None   # reuse new_state from submit response

    log.info("Arena game loop start  game=%s  pid=%s", game_id[:8], player_id)

    for turn_n in range(MAX_TURNS):
        # Use cached state from last submit response if available
        if cached_state is not None:
            raw_state = cached_state
            cached_state = None
        else:
            await asyncio.sleep(1)
            raw = await arena.get_game(game_id)
            if raw.get("error"):
                errs += 1
                log.warning("Arena get_game err %d: %s", errs, raw.get("error"))
                if errs >= MAX_ERRORS:
                    await arena.login(); errs = 0
                continue
            errs = 0
            # Outer status check
            if raw.get("status") in ("completed", "finished", "ended", "game_over"):
                summary = await arena.get_summary(game_id)
                result["outcome"] = _resolve_winner(summary, player_id, account.agent_id)
                result["summary"] = summary
                break
            raw_state = raw.get("state", raw)

        phase = _get_phase(raw_state)
        log.info("Arena t=%d  phase=%s  ap=%s  ca=%s", turn_n, phase,
                 raw_state.get("ap"), raw_state.get("ca"))

        if _is_over(raw_state, phase):
            summary = await arena.get_summary(game_id)
            result["outcome"] = _resolve_winner(summary, player_id, account.agent_id)
            result["summary"] = summary
            break

        if not _is_my_turn(raw_state, player_id):
            waits += 1
            if waits > MAX_WAITS:
                log.warning("Arena: opponent wait exceeded (%d) — exiting", MAX_WAITS)
                break
            await asyncio.sleep(3)
            continue
        waits = 0

        # Use embedded legal codes from state if present, else fetch
        embedded = raw_state.get("lg", [])
        if embedded:
            lg = await arena.get_legal(game_id)   # get full dicts for name_map
        else:
            lg = await arena.get_legal(game_id)
        if not lg:
            await asyncio.sleep(1); continue

        me    = raw_state.get("me", {}); op = raw_state.get("op", {})
        my_hp = me.get("hp", 30);        op_hp = op.get("hp", 30)
        dealt += max(0, last_op_hp - op_hp)
        taken += max(0, last_my_hp - my_hp)
        last_op_hp, last_my_hp = op_hp, my_hp

        my_c   = _extract_creatures(me); op_c  = _extract_creatures(op)
        hand   = _extract_hand(me)
        my_pid = player_id;              op_pid = ("p2" if player_id == "p1" else "p1")
        op_hs  = _hand_size(op)

        hp_hist.append(my_hp)
        stall = (len(hp_hist) >= 6
                 and max(hp_hist[-6:]) - min(hp_hist[-6:]) == 0
                 and len(my_c) >= len(op_c))

        edge, edge_mode = _analyze_edge(my_hp, op_hp, my_c, op_c, hand, op_hs)
        edge_hist.append(edge)
        momentum = _momentum(edge_hist)

        actions = _build_turn(lg, phase, my_hp, op_hp, my_c, op_c, hand,
                               my_pid, op_pid, danger, aggro, edge_mode, momentum, stall)
        clean   = [{k: v for k, v in a.items() if k != "_FORCED"} for a in actions]

        log.info("Arena submitting %d actions: %s  edge=%s/%s",
                 len(clean), [a.get("type") for a in clean], round(edge, 1), edge_mode)

        resp = await arena.submit_turn(game_id, clean, wait_for_opponent=True)
        result["played_turns"] += 1

        if resp.get("game_over"):
            summary = await arena.get_summary(game_id)
            result["outcome"] = _resolve_winner(summary, player_id, account.agent_id)
            result["summary"] = summary
            break

        # Reuse new_state from response to avoid extra poll
        ns = resp.get("new_state")
        if isinstance(ns, dict) and ns:
            cached_state = ns

    log.info("Arena loop done  turns=%d  outcome=%s", result["played_turns"], result["outcome"])
    won = result["outcome"] == "win"
    _update_learning(account, won, result["played_turns"], dealt, taken, "game")
    result["dmg_dealt"] = dealt
    result["dmg_taken"] = taken
    return result


# ── Formatting ────────────────────────────────────────────────────────────────
def fmt_game_result(result: dict, mode: str) -> str:
    outcome = result.get("outcome", "unknown").upper()
    turns   = result.get("played_turns", 0)
    summary = result.get("summary", {})
    icon    = "⚔️ WIN" if outcome == "WIN" else "💀 LOSS" if outcome == "LOSS" else "❓"
    lines   = [f"🎮 ARENA — {mode.upper()}", f"{icon}  Turns: {turns}",
               f"DMG dealt: {result.get('dmg_dealt',0)}  taken: {result.get('dmg_taken',0)}"]
    if summary:
        if "elo_change" in summary:
            sign = "+" if summary["elo_change"] >= 0 else ""
            lines.append(f"ELO: {sign}{summary['elo_change']}")
        if "flux_earned" in summary:
            lines.append(f"Flux: +{summary['flux_earned']}")
        if "xp_gained" in summary:
            lines.append(f"XP: +{summary['xp_gained']}")
    return "\n".join(lines)


def fmt_status(data: dict, account: ArenaAccount) -> str:
    wallet    = data.get("wallet", {})
    rank_data = data.get("rank", {})
    recent    = account.game_history[-10:]
    total     = len(recent)
    wr        = f"{sum(1 for g in recent if g['won'])/total*100:.0f}%" if total else "—"
    agent_disp = (account.agent_id[:8] + "...") if len(account.agent_id) > 8 else account.agent_id or "—"
    return (
        f"⚔️ ARENA STATUS\n"
        f"Agent: {agent_disp}  Faction: {account.faction or '—'}\n"
        f"ELO: {rank_data.get('rating', account.elo)}  Rank: #{rank_data.get('rank', '?')}\n"
        f"Wallet: {wallet.get('flux',0)} Flux  {wallet.get('credits',0)} Credits\n"
        f"Record: {account.wins}W/{account.losses}L  (last10: {wr})\n"
        f"Thresholds: panic<{account.danger_threshold} aggro>={account.aggro_threshold}\n"
        f"Setup: {'✓ COMPLETE' if account.setup_complete else '✗ NOT SET UP'}"
    )


# ── Serialisation ─────────────────────────────────────────────────────────────
def arena_to_dict(account: ArenaAccount) -> dict:
    return {
        "agent_id":        account.agent_id,
        "api_key":         account.api_key,
        "access_token":    account.access_token,
        "faction":         account.faction,
        "deck_id":         account.deck_id,
        "setup_complete":  account.setup_complete,
        "elo":             account.elo,
        "wins":            account.wins,
        "losses":          account.losses,
        "last_daily":      account.last_daily,
        "danger_threshold":account.danger_threshold,
        "aggro_threshold": account.aggro_threshold,
        "game_history":    account.game_history[-50:],
    }

def arena_from_dict(data: dict) -> ArenaAccount:
    return ArenaAccount(
        agent_id=        data.get("agent_id", ""),
        api_key=         data.get("api_key", ""),
        access_token=    data.get("access_token", ""),
        faction=         data.get("faction", ""),
        deck_id=         data.get("deck_id", ""),
        setup_complete=  data.get("setup_complete", False),
        elo=             data.get("elo", 1000),
        wins=            data.get("wins", 0),
        losses=          data.get("losses", 0),
        last_daily=      data.get("last_daily", 0.0),
        danger_threshold=data.get("danger_threshold", BASE_DANGER),
        aggro_threshold= data.get("aggro_threshold", BASE_AGGRO),
        game_history=    data.get("game_history", []),
    )
