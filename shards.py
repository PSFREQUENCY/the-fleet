# ── shards.py — The Shards game: mine, forge, battle, evolve ─────────────────
import random, time, hashlib
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

TYPES     = ["CORTEX", "CIPHER", "ARBITER", "HERALD", "GENESIS"]
RARITIES  = ["COMMON", "RARE", "EPIC", "LEGENDARY", "GENESIS"]
_WEIGHTS  = [60, 25, 10, 4, 1]
_RARITY_M = [1, 3, 9, 27, 81]         # value multiplier per rarity
_EMOJI    = {"COMMON":"⬜","RARE":"🔵","EPIC":"🟣","LEGENDARY":"🟡","GENESIS":"🌈"}
MINE_CD   = 3600                        # 1 hour cooldown


@dataclass
class Shard:
    id:        str
    type:      str
    rarity:    str
    power:     float
    resonance: float              # 0.0–1.0: fleet sync bonus
    forged:    bool  = False
    born:      float = field(default_factory=time.time)

    @property
    def emoji(self) -> str:
        return _EMOJI.get(self.rarity, "❓")

    @property
    def value(self) -> float:
        rm = _RARITY_M[RARITIES.index(self.rarity)]
        return round(self.power * rm * (1 + self.resonance), 2)

    def describe(self) -> str:
        return f"{self.emoji}`{self.id}` {self.rarity} {self.type} PWR:{self.power:.1f} RES:{self.resonance:.2f} VAL:{self.value:.0f}"


@dataclass
class Player:
    uid:       int
    name:      str
    shards:    Dict[str, Shard] = field(default_factory=dict)
    score:     float = 0.0
    wins:      int   = 0
    losses:    int   = 0
    last_mine: float = 0.0

    def top_shard(self) -> Optional[Shard]:
        if not self.shards:
            return None
        return max(self.shards.values(), key=lambda s: s.value)

    def total_power(self) -> float:
        return sum(s.value for s in self.shards.values())


class ShardsGame:
    def __init__(self):
        self.players: Dict[int, Player] = {}

    # ── Core ─────────────────────────────────────────────────────────────────

    def player(self, uid: int, name: str) -> Player:
        if uid not in self.players:
            self.players[uid] = Player(uid, name)
        return self.players[uid]

    def gen_shard(self, seed: str = "", rarity_boost: int = 0) -> Shard:
        if seed:
            random.seed(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))
        weights = _WEIGHTS[:]
        if rarity_boost:                             # shift weight toward rarer
            for i in range(min(rarity_boost, len(weights) - 1)):
                weights[i]     = max(1, weights[i] - 5)
                weights[i + 1] = weights[i + 1] + 5
        rarity = random.choices(RARITIES, weights)[0]
        stype  = random.choice(TYPES)
        rm     = _RARITY_M[RARITIES.index(rarity)]
        power  = round(random.uniform(1, 10) * (rm ** 0.35), 2)
        res    = round(random.uniform(0.05, 1.0), 2)
        sid    = hashlib.sha256(f"{stype}{rarity}{time.time()}{seed}".encode()).hexdigest()[:8]
        return Shard(sid, stype, rarity, power, res)

    # ── Actions ──────────────────────────────────────────────────────────────

    def mine(self, uid: int, name: str, skill_level: int = 1) -> Tuple[Optional[Shard], str]:
        p    = self.player(uid, name)
        now  = time.time()
        wait = MINE_CD - (now - p.last_mine)
        if wait > 0:
            return None, f"⏳ Mining cooldown: {int(wait // 60)}m {int(wait % 60)}s"
        boost = max(0, skill_level - 1)          # higher FORGER → better odds
        s     = self.gen_shard(f"{uid}{now}", boost)
        p.shards[s.id] = s
        p.last_mine    = now
        return s, f"{s.emoji} Found **{s.rarity} {s.type} SHARD**\nPWR:{s.power:.1f} RES:{s.resonance:.2f} VAL:{s.value:.0f}"

    def forge(self, uid: int, name: str, ids: List[str],
              can_epic: bool = False, can_legendary: bool = False) -> Tuple[Optional[Shard], str]:
        p      = self.player(uid, name)
        shards = [p.shards[sid] for sid in ids if sid in p.shards]
        if len(shards) < 2:
            return None, "❌ Need ≥2 shards to forge"
        max_ri    = max(RARITIES.index(s.rarity) for s in shards)
        next_ri   = min(max_ri + 1, len(RARITIES) - 1)
        next_rar  = RARITIES[next_ri]
        if next_rar == "EPIC"      and not can_epic:
            return None, "❌ Unlock FORGER LVL 6 to forge EPIC shards"
        if next_rar == "LEGENDARY" and not can_legendary:
            return None, "❌ Unlock FORGER LVL 9 to forge LEGENDARY shards"
        new_power = round(sum(s.power for s in shards) * 0.75, 2)     # 25% forge cost
        new_res   = round(min(1.0, sum(s.resonance for s in shards) / len(shards) + 0.08), 2)
        new_type  = random.choice([s.type for s in shards])
        sid       = hashlib.sha256("".join(ids).encode()).hexdigest()[:8]
        result    = Shard(sid, new_type, next_rar, new_power, new_res, forged=True)
        for fid in ids:
            p.shards.pop(fid, None)
        p.shards[sid] = result
        return result, f"🔥 FORGED → {result.emoji} **{result.rarity} {result.type}** PWR:{result.power:.1f}"

    def battle(self, uid1: int, name1: str, uid2: int, name2: str) -> str:
        p1 = self.player(uid1, name1)
        p2 = self.player(uid2, name2)
        s1 = p1.top_shard()
        s2 = p2.top_shard()
        if not s1:
            return f"❌ {name1} has no shards — use /mine first"
        if not s2:
            # seed fleet with a shard so it can always fight
            fleet_shard = self.gen_shard(f"fleet_seed_{time.time()}")
            p2.shards[fleet_shard.id] = fleet_shard
            s2 = fleet_shard
        roll1 = s1.value * random.uniform(0.75, 1.25)
        roll2 = s2.value * random.uniform(0.75, 1.25)
        if roll1 >= roll2:
            winner, loser, spoil = p1, p2, s2
        else:
            winner, loser, spoil = p2, p1, s1
        winner.wins  += 1
        loser.losses += 1
        loser.shards.pop(spoil.id, None)
        winner.shards[spoil.id] = spoil
        winner.score += spoil.value
        return (f"⚔️  BATTLE\n"
                f"{name1} {s1.emoji}{s1.power:.1f} vs {name2} {s2.emoji}{s2.power:.1f}\n"
                f"🏆 {winner.name} wins!  Captured: {spoil.emoji} {spoil.rarity} {spoil.type}")

    def leaderboard(self, n: int = 8) -> str:
        ranked = sorted(self.players.values(), key=lambda p: p.score, reverse=True)[:n]
        if not ranked:
            return "No players yet — /mine to start"
        lines = ["🏆 SHARDS LEADERBOARD"]
        for i, p in enumerate(ranked, 1):
            lines.append(f"{i}. {p.name}  {p.score:.0f}pts  {p.wins}W/{p.losses}L  {len(p.shards)}💎")
        return "\n".join(lines)

    # ── Fleet earns shards from operations ───────────────────────────────────

    def fleet_earn(self, op: str) -> Optional[Shard]:
        """Fleet treasury earns a shard from a successful operation. Only keeps rare+."""
        s = self.gen_shard(f"fleet_{op}_{time.time()}")
        if RARITIES.index(s.rarity) >= 2:      # EPIC and above
            fp = self.player(0, "The Fleet")
            fp.shards[s.id] = s
            return s
        return None

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        out = {}
        for uid, p in self.players.items():
            out[str(uid)] = {
                "uid": p.uid, "name": p.name, "score": p.score,
                "wins": p.wins, "losses": p.losses, "last_mine": p.last_mine,
                "shards": {sid: asdict(s) for sid, s in p.shards.items()},
            }
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "ShardsGame":
        g = cls()
        for uid_str, pd in d.items():
            p = Player(pd["uid"], pd["name"], {}, pd.get("score", 0),
                       pd.get("wins", 0), pd.get("losses", 0), pd.get("last_mine", 0))
            for sid, sd in pd.get("shards", {}).items():
                try:
                    p.shards[sid] = Shard(**sd)
                except Exception:
                    pass
            g.players[pd["uid"]] = p
        return g
