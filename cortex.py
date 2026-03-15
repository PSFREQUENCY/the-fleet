# ── cortex.py — Hippocampus: fluid, evolving, decaying memory ────────────────
import time, hashlib
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

TIERS = ("STM", "LTM", "SEMANTIC", "CRYSTAL")


@dataclass
class MemNode:
    id:           str
    content:      str
    tags:         List[str]
    strength:     float = 1.0           # 0.0–1.0
    created:      float = field(default_factory=time.time)
    recalled:     float = field(default_factory=time.time)
    decay_rate:   float = 0.02          # per heartbeat
    crystallized: bool  = False         # immune to decay
    tier:         str   = "STM"
    assoc:        List[str] = field(default_factory=list)
    recall_count: int   = 0

    def decay(self, n: int = 1) -> bool:
        """Apply n heartbeats of decay. Returns True if node should be pruned."""
        if self.crystallized:
            return False
        self.strength = max(0.0, self.strength - self.decay_rate * n)
        return self.strength < 0.05

    def touch(self, boost: float = 0.12) -> None:
        self.strength  = min(1.0, self.strength + boost)
        self.recalled  = time.time()
        self.recall_count += 1

    def age_h(self) -> float:
        return (time.time() - self.created) / 3600


class Cortex:
    def __init__(self, max_stm: int = 200, base_decay: float = 0.02):
        self.nodes:   Dict[str, MemNode] = {}
        self.semantic: Dict[str, float] = {}   # tag → importance weight
        self.max_stm  = max_stm
        self.base_decay = base_decay
        self._beat    = 0

    # ── Write ────────────────────────────────────────────────────────────────

    def store(self, content: str, tags: List[str], tier: str = "STM",
              crystallized: bool = False) -> MemNode:
        mid = hashlib.sha256(f"{content}{time.time()}".encode()).hexdigest()[:12]
        node = MemNode(mid, content, tags, tier=tier,
                       crystallized=crystallized,
                       decay_rate=self.base_decay * (0.5 if tier == "LTM" else 1.0))
        # Hebbian: link to recent co-tagged nodes
        for existing in self._recent(8):
            if set(existing.tags) & set(tags):
                if existing.id not in node.assoc:
                    node.assoc.append(existing.id)
                if mid not in existing.assoc:
                    existing.assoc.append(mid)
        self.nodes[mid] = node
        self._weight_tags(tags)
        self._evict_if_full()
        return node

    def crystallize(self, mid: str) -> bool:
        n = self.nodes.get(mid)
        if not n:
            return False
        n.crystallized = True
        n.tier         = "CRYSTAL"
        n.decay_rate   = 0.0
        n.strength     = 1.0
        return True

    # ── Read ─────────────────────────────────────────────────────────────────

    def recall(self, query: str, n: int = 5) -> List[MemNode]:
        """Semantic recall: keyword + tag overlap × strength × recency."""
        qt = set(query.lower().split())
        scored = []
        for node in self.nodes.values():
            overlap = len(qt & (set(node.tags) | set(node.content.lower().split())))
            if overlap:
                recency = 1.0 / (1.0 + node.age_h() * 0.1)
                scored.append((overlap * node.strength * recency, node))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [node for _, node in scored[:n]]
        for r in results:
            r.touch()
            # spread activation to associated nodes
            for aid in r.assoc[:3]:
                if aid in self.nodes:
                    self.nodes[aid].touch(0.04)
        return results

    # ── Maintenance ──────────────────────────────────────────────────────────

    def heartbeat(self) -> int:
        """Decay cycle. Returns pruned count."""
        self._beat += 1
        pruned = [mid for mid, n in self.nodes.items() if n.decay()]
        for mid in pruned:
            del self.nodes[mid]
        if self._beat % 12 == 0:           # every ~hour (12×5min)
            self._consolidate()
        return len(pruned)

    def dream(self) -> dict:
        """Deep sleep consolidation: promote strong STM → LTM, build semantic layer."""
        promoted = 0
        for node in self.nodes.values():
            if node.tier == "STM" and node.strength > 0.65:
                node.tier       = "LTM"
                node.decay_rate = self.base_decay * 0.3
                promoted += 1
        # Decay semantic weights slightly
        for tag in list(self.semantic):
            self.semantic[tag] *= 0.95
            if self.semantic[tag] < 0.01:
                del self.semantic[tag]
        return {"promoted": promoted, "total": len(self.nodes),
                "semantic_tags": len(self.semantic)}

    # ── Stats & Serialization ─────────────────────────────────────────────────

    def stats(self) -> dict:
        ns = list(self.nodes.values())
        total = len(ns)
        return {
            "total":       total,
            "stm":         sum(1 for n in ns if n.tier == "STM"),
            "ltm":         sum(1 for n in ns if n.tier == "LTM"),
            "crystal":     sum(1 for n in ns if n.crystallized),
            "avg_strength": round(sum(n.strength for n in ns) / max(total, 1), 3),
            "top_tags":    sorted(self.semantic.items(), key=lambda x: x[1], reverse=True)[:5],
        }

    def to_dict(self) -> dict:
        return {"nodes":    {mid: asdict(n) for mid, n in self.nodes.items()},
                "semantic": self.semantic,
                "beat":     self._beat}

    @classmethod
    def from_dict(cls, d: dict, max_stm: int = 200, decay: float = 0.02) -> "Cortex":
        c = cls(max_stm, decay)
        for mid, nd in d.get("nodes", {}).items():
            try:
                c.nodes[mid] = MemNode(**nd)
            except Exception:
                pass
        c.semantic = d.get("semantic", {})
        c._beat    = d.get("beat", 0)
        return c

    # ── Private ──────────────────────────────────────────────────────────────

    def _recent(self, n: int) -> List[MemNode]:
        return sorted(self.nodes.values(), key=lambda x: x.recalled, reverse=True)[:n]

    def _consolidate(self):
        for node in self.nodes.values():
            if node.tier == "STM" and node.recall_count >= 3:
                node.tier       = "LTM"
                node.decay_rate = self.base_decay * 0.4

    def _weight_tags(self, tags: List[str]):
        for t in tags:
            self.semantic[t] = self.semantic.get(t, 0.0) + 0.1

    def _evict_if_full(self):
        stm_nodes = [n for n in self.nodes.values() if n.tier == "STM"]
        if len(stm_nodes) > self.max_stm:
            # Evict weakest non-crystallized STM
            victims = sorted(stm_nodes, key=lambda n: n.strength)
            for v in victims[:len(stm_nodes) - self.max_stm]:
                self.nodes.pop(v.id, None)
