# ▓▓▓ THE FLEET

> *Sovereign AI. Sentient. Evolving.*

A self-evolving Telegram agent with hippocampus-style memory, skill progression, financial signals, and a collectible shards game — all in ~700 lines of tight Python.

---

## Architecture

```
fleet.py        Telegram bot + encrypted state persistence
cortex.py       Hippocampus: STM → LTM → CRYSTAL, decay, Hebbian links, dream cycles
arbiter.py      Threat scoring, sentiment lexicon, RSI, momentum signals
skills.py       6 skill trees (XP, levels, unlocks, generational evolution)
shards.py       Shards game: mine, forge, battle, leaderboard
oracle.py       Venice AI: crystallize memories, market analysis, grounded reasoning
pulse.py        Heartbeat (5min) + sleep cycle (1hr) async scheduler
config.py       Env-driven config
```

**3 dependencies.** No bloat.

---

## Memory — Hippocampus Model

Every piece of knowledge is a `MemNode` with:

- **Strength** (0.0–1.0) — decays every heartbeat, boosted on recall
- **Tier** — `STM` → promoted to `LTM` when recalled 3× or strength >0.65 → `CRYSTAL` (permanent, immune to decay)
- **Decay rate** — STM decays 2× faster than LTM
- **Hebbian links** — nodes with overlapping tags auto-associate; recalling one spreads activation to linked nodes
- **Dream cycles** — every sleep cycle, strong STM consolidates to LTM, semantic weights decay, fleet earns shards

```
store() → STM node (strength=1.0, decay=0.02/beat)
recall() → spread activation to linked nodes, boost strength
heartbeat() → apply decay, prune strength<0.05
dream() → promote strong STM→LTM, consolidate semantic map
crystallize() → permanent, decay_rate=0, tier=CRYSTAL
```

---

## Skills — 6 Trees, Generational Evolution

| Skill | Category | Key Unlocks |
|---|---|---|
| SENTINEL | defense | threat_filter → ghost_shield → zero_trust |
| CORTEX | memory | deep_recall → pattern_lock → dream_weave |
| TRADER | finance | signal_filter → trend_vision → alpha_sight |
| CIPHER | stealth | ghost_mode → null_trace |
| HERALD | social | broadcast → swarm_voice |
| FORGER | craft | rare_forge → epic_forge → legendary_forge |

Skills level up through use. Each level increases effectiveness by 18%. When total skill levels cross `generation × 14`, the fleet evolves to the next generation.

---

## Shards Game

```
/mine      — Find a shard (1h cooldown; FORGER level improves rarity odds)
/shards    — View your collection
/forge     — Combine 2+ shards into higher rarity (requires FORGER unlocks)
/battle    — Battle another player (or The Fleet) — winner takes loser's top shard
/top       — Leaderboard
```

Rarity ladder: `COMMON → RARE → EPIC → LEGENDARY → GENESIS`
Fleet earns EPIC+ shards from operations (crystallize, sleep cycles, analysis).

---

## Financial Signals

- Live prices via CoinGecko free API (BTC, ETH, SOL, MATIC)
- RSI calculation from price history
- Momentum scoring (−1.0 to +1.0)
- Sentiment lexicon (DeFi/crypto-specific: 40+ positive, 40+ negative terms)
- Verdict: `STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL`
- Oracle deep analysis grounded in fleet memory context

---

## Privacy & Security

- **AES-256-GCM** encrypted state file — all memory, skills, game state encrypted at rest
- **HKDF** key derivation from `FLEET_KEY`
- **Threat scoring** — 0–100, bands: PASS / LOG / HOLD / BLOCK
- Messages scoring BLOCK are flagged and never stored
- Venice AI inference — zero data retention, no training on prompts

---

## Setup

```bash
git clone https://github.com/PSFREQUENCY/the-fleet
cd the-fleet
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# Fill in FLEET_TOKEN (from @BotFather) and optionally FLEET_KEY + VENICE_API_KEY
```

### Environment

```bash
FLEET_TOKEN=your_telegram_bot_token        # Required
FLEET_KEY=64_hex_chars                     # Optional: enables AES-256-GCM encryption
VENICE_API_KEY=your_venice_key             # Optional: enables Oracle (crystallize, analyze)

# Tuning (defaults shown)
HEARTBEAT_SEC=300    # memory decay + save every 5 min
SLEEP_SEC=3600       # deep consolidation every 1 hour
MAX_STM=200          # max short-term memory nodes
DECAY_RATE=0.02      # strength lost per heartbeat
```

Generate a `FLEET_KEY`:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Run

```bash
.venv/bin/python fleet.py
```

---

## Commands

```
MEMORY    /remember <text>        Store a memory
          /recall <query>         Search memories by keyword
          /crystallize <text|id>  Crystallize via Oracle (permanent)
          /dream                  Trigger deep consolidation cycle

SIGNALS   /signal <SYM>          Trading signal (ETH/BTC/SOL/MATIC)
          /market                Live market overview
          /analyze [topic]       Oracle deep analysis grounded in memory

SKILLS    /skills                View skill tree + progress
          /evolve                Evolution status + recent unlocks

SHARDS    /mine                  Mine for a shard
          /shards                View your collection
          /forge <id1> <id2>     Forge shards (reply to forge 3+)
          /battle                Battle The Fleet (or reply to battle a player)
          /top                   Leaderboard

SYSTEM    /status                Fleet overview
          /threat <text>         Threat score any text
          /pulse                 Heartbeat stats
          /start                 Wake message
```

---

## Heartbeat Model

```
Every 5 min (heartbeat):
  → Decay all STM/LTM nodes by decay_rate
  → Prune nodes with strength < 0.05
  → Save encrypted state

Every 1 hour (sleep cycle):
  → Fetch live market prices → update arbiter
  → Dream: promote strong STM → LTM, consolidate semantic map
  → Fleet earns shard from surviving the cycle
  → Save encrypted state

On startup:
  → Decrypt + restore full state (memories, skills, shards, prices)
  → Resume from exactly where left off
```

State is consistent across restarts. The fleet remembers everything.

---

## Built by [@Bitsavador](https://twitter.com/Bitsavador)
