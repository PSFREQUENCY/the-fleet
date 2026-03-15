# ── arbiter.py — Threat scoring, sentiment, financial signals ────────────────
import re, time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

BANDS = [(75, "BLOCK"), (50, "HOLD"), (25, "LOG"), (0, "PASS")]


@dataclass
class Signal:
    symbol:     str
    price:      float
    change_24h: float
    sentiment:  float       # -1.0 to 1.0
    momentum:   float       # -1.0 to 1.0
    ts:         float = field(default_factory=time.time)

    @property
    def verdict(self) -> str:
        s, m = self.sentiment, self.momentum
        if s >  0.4 and m >  0.3: return "STRONG_BUY"
        if s >  0.1 and m >  0.0: return "BUY"
        if s < -0.4 and m < -0.3: return "STRONG_SELL"
        if s < -0.1 and m <  0.0: return "SELL"
        return "NEUTRAL"

    @property
    def emoji(self) -> str:
        return {"STRONG_BUY": "🟢🟢", "BUY": "🟢", "STRONG_SELL": "🔴🔴",
                "SELL": "🔴", "NEUTRAL": "⚪"}.get(self.verdict, "⚪")


class Arbiter:
    _POS  = {"moon","pump","bullish","buy","long","green","surge","breakout","up",
             "gain","profit","rip","gm","wagmi","strong","alpha","ath","accumulate"}
    _NEG  = {"dump","crash","bearish","sell","short","red","rug","scam","down",
             "loss","rekt","ngmi","dead","capitulate","collapse","fud","hack","exit"}
    _RISK = {"phishing","malicious","exploit","drain","attack","suspicious",
             "unauthorized","breach","seedphrase","privatekey","mnemonic"}

    def __init__(self):
        self.scores:        List            = []          # (ts, score, band)
        self.price_history: Dict[str, List] = {}          # sym → [(ts, price)]

    # ── Threat ───────────────────────────────────────────────────────────────

    def threat(self, text: str) -> Tuple[int, str]:
        t = text.lower()
        score = 0
        if any(w in t for w in self._RISK):                            score += 40
        if re.search(r'(private|seed|mnemonic|passphrase|password)', t): score += 35
        if re.search(r'(click|link|verify|urgent|confirm)\s+(here|now)', t): score += 30
        if re.search(r'0x[a-fA-F0-9]{40}', text):                     score += 10
        if re.search(r'https?://', text) and any(w in t for w in self._RISK): score += 15
        score = min(score, 100)
        band  = next(b for s, b in BANDS if score > s)
        self.scores.append([time.time(), score, band])
        if len(self.scores) > 2000:
            self.scores = self.scores[-1000:]
        return score, band

    # ── Sentiment ────────────────────────────────────────────────────────────

    def sentiment(self, text: str) -> float:
        words = set(text.lower().split())
        pos   = len(words & self._POS)
        neg   = len(words & self._NEG)
        total = pos + neg
        return 0.0 if not total else (pos - neg) / total

    # ── Financial ────────────────────────────────────────────────────────────

    def tick(self, symbol: str, price: float) -> None:
        h = self.price_history.setdefault(symbol, [])
        h.append([time.time(), price])
        self.price_history[symbol] = h[-300:]

    def momentum(self, symbol: str, window: int = 14) -> float:
        hist = self.price_history.get(symbol, [])
        prices = [p for _, p in hist[-window:]]
        if len(prices) < 2 or prices[0] == 0:
            return 0.0
        return max(-1.0, min(1.0, (prices[-1] - prices[0]) / prices[0] * 10))

    def signal(self, symbol: str, price: float, text: str = "") -> Signal:
        self.tick(symbol, price)
        hist  = self.price_history.get(symbol, [])
        ch24  = 0.0
        if len(hist) >= 2 and hist[0][1]:
            ch24 = (hist[-1][1] - hist[0][1]) / hist[0][1]
        return Signal(symbol, price, ch24,
                      self.sentiment(text), self.momentum(symbol))

    # ── RSI-like score (0–100) ───────────────────────────────────────────────

    def rsi(self, symbol: str, period: int = 14) -> float:
        hist   = self.price_history.get(symbol, [])
        prices = [p for _, p in hist[-period - 1:]]
        if len(prices) < 2:
            return 50.0
        gains  = [max(0, prices[i] - prices[i-1]) for i in range(1, len(prices))]
        losses = [max(0, prices[i-1] - prices[i]) for i in range(1, len(prices))]
        ag, al = sum(gains) / len(gains), sum(losses) / len(losses)
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    # ── Stats ────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        if not self.scores:
            return {"scanned": 0, "avg_score": 0, "blocked": 0, "pass_rate": 1.0}
        recent = self.scores[-100:]
        n = len(recent)
        return {
            "scanned":   len(self.scores),
            "avg_score": round(sum(s[1] for s in recent) / n, 1),
            "blocked":   sum(1 for s in recent if s[2] == "BLOCK"),
            "pass_rate": round(sum(1 for s in recent if s[2] == "PASS") / n, 2),
        }
