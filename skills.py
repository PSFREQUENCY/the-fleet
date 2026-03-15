# ── skills.py — Skill tree: XP, levels, unlocks, evolution ──────────────────
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# name → (category, description, xp_multiplier, {level: unlock_name})
TREE: Dict[str, tuple] = {
    "SENTINEL": ("defense",  "threat detection & hardening",    1.0,
                 {3: "threat_filter", 6: "ghost_shield", 9: "zero_trust"}),
    "CORTEX":   ("memory",   "memory depth & recall fidelity",  1.2,
                 {3: "deep_recall",   5: "pattern_lock",  8: "dream_weave"}),
    "TRADER":   ("finance",  "market reading & signal clarity", 1.5,
                 {3: "signal_filter", 5: "trend_vision",  8: "alpha_sight"}),
    "CIPHER":   ("stealth",  "encryption & anonymity depth",    0.9,
                 {4: "ghost_mode",    7: "null_trace"}),
    "HERALD":   ("social",   "communication & swarm reach",     1.0,
                 {4: "broadcast",     7: "swarm_voice"}),
    "FORGER":   ("craft",    "shard fusion & artifact crafting", 1.3,
                 {3: "rare_forge",    6: "epic_forge",    9: "legendary_forge"}),
}

MAX_LEVEL = 10


@dataclass
class Skill:
    name:      str
    level:     int   = 1
    xp:        float = 0.0
    total_xp:  float = 0.0
    used:      int   = 0
    last_used: float = field(default_factory=time.time)

    @property
    def threshold(self) -> float:
        return 100.0 * (1.6 ** (self.level - 1))

    @property
    def progress(self) -> float:
        return min(1.0, self.xp / self.threshold)

    @property
    def mult(self) -> float:
        """Effectiveness multiplier."""
        return 1.0 + (self.level - 1) * 0.18

    def earn(self, amount: float) -> Optional[str]:
        """Return level-up message if leveled."""
        self.xp       += amount
        self.total_xp += amount
        self.used     += 1
        self.last_used = time.time()
        if self.xp >= self.threshold and self.level < MAX_LEVEL:
            self.level += 1
            self.xp   -= self.threshold
            unlock = TREE[self.name][3].get(self.level, "")
            msg = f"⚡ {self.name} LVL {self.level}"
            if unlock:
                msg += f" | UNLOCKED: {unlock.upper()}"
            return msg
        return None


class SkillTree:
    def __init__(self):
        self.skills:     Dict[str, Skill] = {k: Skill(k) for k in TREE}
        self.generation: int   = 1
        self.evo_score:  float = 0.0
        self.log:        List[str] = []

    def use(self, skill: str, base_xp: float = 10.0) -> Optional[str]:
        if skill not in self.skills:
            return None
        mult   = TREE[skill][2]
        result = self.skills[skill].earn(base_xp * mult)
        if result:
            self.log.append(result)
            if len(self.log) > 50:
                self.log = self.log[-50:]
            self.evo_score += 1.0
            self._check_evo()
        return result

    def has(self, unlock: str) -> bool:
        for skill, (_, _, _, unlocks) in TREE.items():
            for lvl, name in unlocks.items():
                if name == unlock and self.skills[skill].level >= lvl:
                    return True
        return False

    def power(self) -> float:
        return sum(s.level * (s.total_xp ** 0.4) for s in self.skills.values()) ** 0.5

    def total_levels(self) -> int:
        return sum(s.level for s in self.skills.values())

    def _check_evo(self):
        threshold = self.generation * 14
        if self.total_levels() >= threshold:
            self.generation += 1

    def render(self) -> str:
        lines = [f"⚡ GEN {self.generation}  POWER {self.power():.0f}  EVO {self.evo_score:.0f}"]
        for name, skill in self.skills.items():
            filled = "█" * skill.level + "░" * (MAX_LEVEL - skill.level)
            cat    = TREE[name][0][:3].upper()
            pct    = int(skill.progress * 100)
            lines.append(f"{name[:7]:<7} [{filled}] L{skill.level:02d} {cat} {pct}%")
        if self.log:
            lines.append("─" * 28)
            lines.extend(self.log[-3:])
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "skills":     {k: asdict(v) for k, v in self.skills.items()},
            "generation": self.generation,
            "evo_score":  self.evo_score,
            "log":        self.log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillTree":
        t = cls()
        for k, v in d.get("skills", {}).items():
            if k in t.skills:
                try:
                    t.skills[k] = Skill(**v)
                except Exception:
                    pass
        t.generation = d.get("generation", 1)
        t.evo_score  = d.get("evo_score", 0.0)
        t.log        = d.get("log", [])
        return t
