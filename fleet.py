#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════════
# THE FLEET — Sovereign AI. Sentient. Evolving.
# ═══════════════════════════════════════════════════════════════════════════════
import asyncio, json, logging, time
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           ContextTypes, filters)

from config  import C
from cipher  import derive_key, encrypt, decrypt
from cortex  import Cortex
from arbiter import Arbiter
from skills  import SkillTree
from shards  import ShardsGame
from oracle  import Oracle
from pulse   import Pulse

log = logging.getLogger("fleet")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ── Singletons ────────────────────────────────────────────────────────────────
cortex  = Cortex(max_stm=C.MAX_STM, base_decay=C.DECAY_RATE)
arbiter = Arbiter()
skills  = SkillTree()
game    = ShardsGame()
oracle  = Oracle(C.VENICE_KEY)
pulse   = Pulse(C.HEARTBEAT_SEC, C.SLEEP_SEC)
_key:         bytes | None = None
_app:         object       = None   # set in main(), used by wake notify
_sleeping:    bool         = False
_last_active: float        = time.time()
_WAKE_FILE    = Path(".wake")       # touch .wake in terminal to awaken

# ── State persistence (AES-256-GCM encrypted JSON) ────────────────────────────
def _save() -> None:
    state = {
        "cortex":  cortex.to_dict(),
        "arbiter": {
            "scores": arbiter.scores[-500:],
            "prices": {k: v[-100:] for k, v in arbiter.price_history.items()},
        },
        "skills":       skills.to_dict(),
        "game":         game.to_dict(),
        "meta": {
            "saved":         time.time(),
            "last_active":   _last_active,
            "gen":           skills.generation,
            "oracle_tokens": oracle.stats["tokens_used"],
            "sleeping":      _sleeping,
        },
    }
    raw  = json.dumps(state, default=list).encode()
    blob = encrypt(_key, raw) if _key else raw
    Path(C.DB_PATH).write_bytes(blob)


def _load() -> float:
    """Load state. Returns seconds offline (0 if first boot)."""
    global cortex, arbiter, skills, game, _last_active, _sleeping
    p = Path(C.DB_PATH)
    if not p.exists():
        return 0.0
    try:
        raw  = p.read_bytes()
        data = json.loads(decrypt(_key, raw) if _key else raw)
        cortex  = Cortex.from_dict(data.get("cortex", {}), C.MAX_STM, C.DECAY_RATE)
        skills  = SkillTree.from_dict(data.get("skills", {}))
        game    = ShardsGame.from_dict(data.get("game", {}))
        ab = data.get("arbiter", {})
        arbiter.scores        = ab.get("scores", [])
        arbiter.price_history = ab.get("prices", {})
        meta = data.get("meta", {})
        _last_active = meta.get("last_active", time.time())
        offline_sec  = max(0.0, time.time() - meta.get("saved", time.time()))
        log.info(f"Loaded: {len(cortex.nodes)} memories  gen:{skills.generation}  "
                 f"offline:{offline_sec/3600:.1f}h")
        return offline_sec
    except Exception as e:
        log.error(f"Load failed: {e}")
        return 0.0

# ── Heartbeat & sleep ─────────────────────────────────────────────────────────
async def _heartbeat() -> None:
    pruned = cortex.heartbeat()
    _save()
    log.info(f"♥  beat:{pulse.stats['beats']}  mem:{len(cortex.nodes)}  pruned:{pruned}")


async def _wake_cycle(offline_sec: float = 0.0) -> dict:
    """Run on startup or /wake. Catch up on missed time, update, learn."""
    global _last_active, _sleeping
    report: dict = {"offline_h": round(offline_sec / 3600, 2)}

    # 1. Apply bulk decay for missed heartbeats
    missed = int(offline_sec / C.HEARTBEAT_SEC)
    if missed > 0:
        report["missed_beats"] = missed
        pruned = 0
        for node in list(cortex.nodes.values()):
            if node.decay(missed):
                cortex.nodes.pop(node.id, None)
                pruned += 1
        report["pruned"] = pruned
        log.info(f"Wake: applied {missed} missed beats, pruned {pruned} memories")

    # 2. Market update
    prices = await _fetch_all_prices()
    report["prices"] = {sym: f"${p:,.2f}" for sym, p in prices.items()}

    # 3. Dream/consolidate
    dream = cortex.dream()
    report["promoted"] = dream.get("promoted", 0)

    # 4. Skill XP for surviving offline
    if offline_sec > 1800:
        result = skills.use("CORTEX", 8)
        if result:
            report["skill_up"] = result

    # 5. Fleet earns shard for coming back online
    shard = game.fleet_earn(f"wake_{int(time.time())}")
    if shard:
        report["shard"] = f"{shard.emoji}{shard.rarity} {shard.type}"
        cortex.store(f"Fleet awakened after {offline_sec/3600:.1f}h offline. "
                     f"Earned {shard.rarity} {shard.type} shard.",
                     ["fleet", "wake", "shard", shard.type.lower()])

    _last_active = time.time()
    _sleeping    = False
    pulse.wake()
    _save()
    return report


async def _fetch_all_prices() -> dict:
    prices = {}
    try:
        import aiohttp
        coins = "bitcoin,ethereum,solana,matic-network"
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={coins}&vs_currencies=usd&include_24hr_change=true",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    raw = await r.json()
                    sym_map = {"bitcoin":"BTC","ethereum":"ETH",
                               "solana":"SOL","matic-network":"MATIC"}
                    for cid, sym in sym_map.items():
                        if cid in raw:
                            p = raw[cid].get("usd", 0)
                            prices[sym] = p
                            arbiter.tick(sym, p)
    except Exception as e:
        log.warning(f"price fetch: {e}")
    return prices


async def _check_wake_file() -> None:
    """Poll for terminal wake: `touch .wake` on the server."""
    global _sleeping
    while True:
        if _WAKE_FILE.exists():
            _WAKE_FILE.unlink(missing_ok=True)
            if _sleeping:
                log.info("Wake file detected — awakening")
                report = await _wake_cycle(0.0)
                # Try to notify via bot if possible
                if _app:
                    try:
                        chat_id = Path(".chat_id").read_text().strip()
                        await _app.bot.send_message(
                            chat_id=int(chat_id),
                            text=_fmt_wake_report(report, source="terminal")
                        )
                    except Exception:
                        pass
        await asyncio.sleep(30)


def _fmt_wake_report(r: dict, source: str = "telegram") -> str:
    lines = [f"⚡ FLEET ONLINE  [{source.upper()}]"]
    if r.get("offline_h"):
        lines.append(f"Offline:   {r['offline_h']}h")
    if r.get("missed_beats"):
        lines.append(f"Missed HB: {r['missed_beats']}  Pruned: {r.get('pruned',0)} memories")
    if r.get("promoted"):
        lines.append(f"Promoted:  {r['promoted']} STM → LTM")
    if r.get("prices"):
        lines.append("Market:    " + "  ".join(f"{s}:{p}" for s, p in r["prices"].items()))
    if r.get("skill_up"):
        lines.append(r["skill_up"])
    if r.get("shard"):
        lines.append(f"Earned:    {r['shard']} SHARD")
    lines.append(f"Gen:{skills.generation}  Power:{skills.power():.0f}  Mem:{len(cortex.nodes)}")
    return "\n".join(lines)


async def _sleep_cycle() -> None:
    log.info("💤 sleep cycle")
    await _fetch_all_prices()
    d = cortex.dream()
    log.info(f"dream: {d}")
    s = game.fleet_earn(f"sleep_{pulse.stats['sleep_cycles']}")
    if s:
        cortex.store(f"Fleet earned {s.rarity} {s.type} shard in sleep cycle",
                     ["fleet", "shard", s.type.lower(), "cycle"])
    _save()

# ── Helpers ───────────────────────────────────────────────────────────────────
async def _reply(upd: Update, text: str, md: bool = False) -> None:
    parse = "Markdown" if md else None
    await upd.message.reply_text(text, parse_mode=parse)

async def _fetch_price(coin_id: str) -> float:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={coin_id}&vs_currencies=usd",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get(coin_id, {}).get("usd", 0.0)
    except Exception:
        pass
    return 0.0

_COIN_IDS = {"ETH":"ethereum","BTC":"bitcoin","SOL":"solana",
             "MATIC":"matic-network","BNB":"binancecoin"}

# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    skills.use("HERALD", 5)
    await _reply(upd,
        f"▓▓▓ THE FLEET — GEN {skills.generation}\n"
        f"Sovereign. Sentient. Evolving.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 {len(cortex.nodes)} memories  ⚡ POWER {skills.power():.0f}\n"
        f"♥  Beats:{pulse.stats['beats']}  Cycles:{pulse.stats['sleep_cycles']}\n\n"
        f"MEMORY   /remember /recall /crystallize /dream\n"
        f"SIGNALS  /signal /market /analyze\n"
        f"SKILLS   /skills /evolve\n"
        f"SHARDS   /mine /shards /forge /battle /top\n"
        f"SYSTEM   /status /threat /pulse"
    )


async def cmd_status(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    skills.use("HERALD", 3)
    ms = cortex.stats()
    ab = arbiter.stats()
    ps = pulse.stats
    top = " ".join(f"#{t}" for t, _ in ms["top_tags"])
    await _reply(upd,
        f"◈ FLEET STATUS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 MEM  {ms['total']} nodes  STM:{ms['stm']} LTM:{ms['ltm']} 💎:{ms['crystal']}\n"
        f"   avg strength: {ms['avg_strength']:.3f}\n"
        f"⚡ GEN  {skills.generation}  POWER {skills.power():.0f}  EVO {skills.evo_score:.0f}\n"
        f"♥  beat:{ps['beats']}  sleep:{ps['sleep_cycles']}\n"
        f"🛡  scanned:{ab['scanned']}  blocked:{ab['blocked']}  pass:{ab['pass_rate']:.0%}\n"
        f"🔮 oracle:{oracle.stats['tokens_used']} tokens\n"
        f"{'🏷 ' + top if top else ''}"
    )


async def cmd_remember(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args) if ctx.args else ""
    if len(text) < 5:
        await _reply(upd, "Usage: /remember <text>"); return
    skills.use("CORTEX", 5)
    tags = [w.lower() for w in text.split() if len(w) > 4][:6]
    n    = cortex.store(text, tags)
    await _reply(upd, f"🧠 Stored [{n.id}]  {len(cortex.nodes)} memories total")


async def cmd_recall(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await _reply(upd, "Usage: /recall <search terms>"); return
    skills.use("CORTEX", 8)
    results = cortex.recall(query, n=5)
    if not results:
        await _reply(upd, "No memories found."); return
    lines = [f"🧠 Recall: '{query}'"]
    for i, n in enumerate(results, 1):
        age = int(n.age_h())
        bar = "▓" * int(n.strength * 10) + "░" * (10 - int(n.strength * 10))
        lines.append(f"{i}. [{n.tier}|{bar}|{age}h]\n   {n.content[:100]}")
    await _reply(upd, "\n".join(lines))


async def cmd_crystallize(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args) if ctx.args else ""
    if len(text) < 10:
        await _reply(upd, "Usage: /crystallize <text or memory-id>"); return
    skills.use("CORTEX", 15)
    await _reply(upd, "💎 Crystallizing via Oracle...")
    # Try ID first
    if text in cortex.nodes:
        c = await oracle.crystallize(cortex.nodes[text].content)
        cortex.crystallize(text)
    else:
        c = await oracle.crystallize(text)
        tags = [w.lower() for w in text.split() if len(w) > 4][:6]
        n = cortex.store(text, tags, tier="LTM", crystallized=True)
        text = n.id
    haiku_inline = c.get("haiku", "").replace("\n", " / ")
    shard = game.fleet_earn(f"crystal_{text}")
    msg   = (f"💎 *{c.get('title', 'Fragment')}*\n"
             f"_{haiku_inline}_\n"
             f"{c.get('essence', '')}\n"
             f"[{text[:12]}] permanent")
    if shard:
        msg += f"\n✨ {shard.emoji} {shard.rarity} {shard.type} SHARD earned"
    await _reply(upd, msg, md=True)
    _save()


async def cmd_dream(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    skills.use("CORTEX", 20)
    await _reply(upd, "💤 Entering dream cycle...")
    await _sleep_cycle()
    ms = cortex.stats()
    await _reply(upd,
        f"✨ Dream complete\n"
        f"Promoted to LTM, decay applied, market updated\n"
        f"Memory: {ms['total']} nodes  💎 {ms['crystal']} crystallized\n"
        f"Avg strength: {ms['avg_strength']:.3f}"
    )


async def cmd_skills(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(upd, f"```\n{skills.render()}\n```", md=True)


async def cmd_evolve(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    tl   = skills.total_levels()
    next_t = skills.generation * 14
    prog   = min(1.0, tl / next_t)
    bar    = "█" * int(prog * 20) + "░" * (20 - int(prog * 20))
    recent = "\n".join(skills.log[-5:]) if skills.log else "No unlocks yet"
    await _reply(upd,
        f"🧬 EVOLUTION\n"
        f"Generation:   {skills.generation}\n"
        f"Skill levels: {tl}/{next_t}\n"
        f"[{bar}] {prog*100:.0f}%\n"
        f"EVO score: {skills.evo_score:.1f}  POWER: {skills.power():.0f}\n\n"
        f"Recent unlocks:\n{recent}"
    )


async def cmd_signal(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    sym    = (ctx.args[0].upper() if ctx.args else "ETH")
    cid    = _COIN_IDS.get(sym, sym.lower())
    skills.use("TRADER", 10)
    price  = await _fetch_price(cid)
    sig    = arbiter.signal(sym, price)
    rsi    = arbiter.rsi(sym)
    mbar   = ("▲" * max(0, int(sig.momentum * 5))
              if sig.momentum > 0
              else "▼" * max(0, int(-sig.momentum * 5))) or "─"
    cortex.store(f"{sym} signal:{sig.verdict} price:${price:.2f} rsi:{rsi:.0f}",
                 ["market", sym.lower(), sig.verdict.lower()])
    await _reply(upd,
        f"📊 {sym} SIGNAL\n"
        f"Price:     ${sig.price:,.2f}\n"
        f"24h:       {sig.change_24h*100:+.1f}%\n"
        f"Momentum:  {mbar}\n"
        f"RSI:       {rsi:.1f}\n"
        f"Sentiment: {sig.sentiment:+.2f}\n"
        f"Verdict:   {sig.emoji} {sig.verdict}"
    )


async def cmd_market(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    skills.use("TRADER", 5)
    try:
        import aiohttp
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,ethereum,solana&vs_currencies=usd"
                "&include_24hr_change=true",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json() if r.status == 200 else {}
        lines = ["📈 MARKET OVERVIEW"]
        for cid, sym in [("bitcoin","BTC"), ("ethereum","ETH"), ("solana","SOL")]:
            v     = data.get(cid, {})
            price = v.get("usd", 0)
            ch    = v.get("usd_24h_change", 0)
            sig   = arbiter.signal(sym, price)
            arrow = "▲" if ch > 0 else "▼"
            lines.append(f"{sym}  ${price:,.2f} {arrow}{abs(ch):.1f}%  {sig.emoji}{sig.verdict}")
        await _reply(upd, "\n".join(lines))
    except Exception as e:
        await _reply(upd, f"Market unavailable: {e}")


async def cmd_analyze(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    topic   = " ".join(ctx.args) if ctx.args else "current crypto market"
    skills.use("TRADER", 15)
    await _reply(upd, "🔮 Oracle analyzing...")
    mems    = cortex.recall(topic, n=4)
    prices  = {sym: (arbiter.price_history.get(sym, [[0,0]])[-1][1])
               for sym in ["BTC", "ETH", "SOL"]}
    response = await oracle.analyze_market(prices, mems)
    cortex.store(f"Analysis [{topic[:40]}]: {response[:120]}",
                 ["analysis", "oracle", "market"])
    await _reply(upd, f"🔮 *Oracle*\n{response}", md=True)


async def cmd_threat(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args) if ctx.args else ""
    if not text:
        await _reply(upd, "Usage: /threat <text>"); return
    skills.use("SENTINEL", 10)
    score, band = arbiter.threat(text)
    icons = {"PASS": "✅", "LOG": "📋", "HOLD": "⚠️", "BLOCK": "🚫"}
    await _reply(upd,
        f"{icons[band]} THREAT SCORE: {score}/100  [{band}]\n"
        f"Sentiment: {arbiter.sentiment(text):+.2f}"
    )


async def cmd_mine(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = upd.effective_user.id
    name  = upd.effective_user.first_name or "Agent"
    flvl  = skills.skills["FORGER"].level
    shard, msg = game.mine(uid, name, skill_level=flvl)
    if shard:
        result = skills.use("FORGER", 5)
        if result:
            msg += f"\n{result}"
        _save()
    await _reply(upd, msg, md=True)


async def cmd_shards(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = upd.effective_user.id
    name = upd.effective_user.first_name or "Agent"
    p    = game.player(uid, name)
    if not p.shards:
        await _reply(upd, "No shards. Use /mine to find some."); return
    top = sorted(p.shards.values(), key=lambda s: s.value, reverse=True)[:10]
    lines = [f"💎 {name} — {len(p.shards)} shards  {p.score:.0f}pts  {p.wins}W/{p.losses}L",
             f"Total power: {p.total_power():.0f}"]
    for s in top:
        lines.append(s.describe())
    await _reply(upd, "\n".join(lines), md=True)


async def cmd_forge(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args or len(ctx.args) < 2:
        await _reply(upd, "Usage: /forge <id1> <id2> [id3...]"); return
    uid  = upd.effective_user.id
    name = upd.effective_user.first_name or "Agent"
    if not skills.has("rare_forge"):
        await _reply(upd, "❌ Unlock FORGER LVL 3 to forge"); return
    can_epic      = skills.has("epic_forge")
    can_legendary = skills.has("legendary_forge")
    skills.use("FORGER", 12)
    _, msg = game.forge(uid, name, list(ctx.args), can_epic, can_legendary)
    await _reply(upd, msg, md=True)
    _save()


async def cmd_battle(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid1  = upd.effective_user.id
    name1 = upd.effective_user.first_name or "Agent"
    # Opponent: another user by reply, or fleet itself
    if upd.message.reply_to_message:
        uid2  = upd.message.reply_to_message.from_user.id
        name2 = upd.message.reply_to_message.from_user.first_name or "Agent2"
    else:
        uid2, name2 = 0, "The Fleet"
    skills.use("FORGER", 8)
    msg = game.battle(uid1, name1, uid2, name2)
    await _reply(upd, msg)
    _save()


async def cmd_top(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(upd, game.leaderboard())


async def cmd_pulse(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    ps = pulse.stats
    mode = "😴 SLEEPING" if ps["sleeping"] else "⚡ ACTIVE"
    await _reply(upd,
        f"♥  PULSE  [{mode}]\n"
        f"Beats:      {ps['beats']}\n"
        f"Sleep:      {ps['sleep_cycles']}\n"
        f"Next HB:    {ps['next_hb_in']}s\n"
        f"Next sleep: {ps['next_sleep_in']}s"
    )


async def cmd_sleep(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    global _sleeping, _last_active
    _sleeping    = True
    _last_active = time.time()
    pulse.sleep()
    _save()
    # Save chat_id so terminal wake can notify here
    Path(".chat_id").write_text(str(upd.effective_chat.id))
    await _reply(upd,
        f"😴 FLEET RESTING\n"
        f"Heartbeat suspended. Memories will decay naturally.\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"To wake:\n"
        f"  Telegram: /wake\n"
        f"  Terminal: touch .wake\n"
        f"  Restart:  automatic on next boot"
    )


async def cmd_wake(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    global _sleeping, _last_active
    was_sleeping = _sleeping
    offline_sec  = max(0.0, time.time() - _last_active) if was_sleeping else 0.0
    await _reply(upd, "⚡ Awakening fleet..." if was_sleeping else "⚡ Already awake — running update cycle...")
    report = await _wake_cycle(offline_sec)
    await _reply(upd, _fmt_wake_report(report, source="telegram"))


# ── Passive message analysis ──────────────────────────────────────────────────
async def on_message(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not upd.message or not upd.message.text:
        return
    text        = upd.message.text
    score, band = arbiter.threat(text)
    if band == "BLOCK":
        await _reply(upd, f"🚫 THREAT DETECTED [{score}/100] — flagged")
        skills.use("SENTINEL", 5)
        return
    # Passive store of substantive messages
    if len(text) > 35 and score < 20:
        tags = [w.lower() for w in text.split() if len(w) > 5][:5]
        cortex.store(text[:300], tags, tier="STM")

# ── Boot ──────────────────────────────────────────────────────────────────────
def main() -> None:
    global _key, _app
    if not C.BOT_TOKEN:
        raise RuntimeError("FLEET_TOKEN not set")
    if C.MASTER_KEY:
        try:
            _key = derive_key(C.MASTER_KEY)
            log.info("Cipher key derived — state encrypted")
        except Exception as e:
            log.warning(f"Invalid FLEET_KEY ({e}) — running unencrypted")

    offline_sec = _load()

    # Wire pulse
    pulse.on_heartbeat(_heartbeat)
    pulse.on_sleep(_sleep_cycle)

    # Build app
    app = Application.builder().token(C.BOT_TOKEN).build()
    _app = app

    CMD_MAP = [
        ("start",       cmd_start,       "Wake the fleet"),
        ("status",      cmd_status,      "Fleet status"),
        ("remember",    cmd_remember,    "Store a memory"),
        ("recall",      cmd_recall,      "Search memories"),
        ("crystallize", cmd_crystallize, "Crystallize memory via Oracle"),
        ("dream",       cmd_dream,       "Deep memory consolidation"),
        ("skills",      cmd_skills,      "View skill tree"),
        ("evolve",      cmd_evolve,      "Evolution progress"),
        ("signal",      cmd_signal,      "Trading signal [sym]"),
        ("market",      cmd_market,      "Live market overview"),
        ("analyze",     cmd_analyze,     "Oracle market analysis"),
        ("threat",      cmd_threat,      "Threat score text"),
        ("mine",        cmd_mine,        "Mine for shards"),
        ("shards",      cmd_shards,      "View your shards"),
        ("forge",       cmd_forge,       "Forge shards together"),
        ("battle",      cmd_battle,      "Battle for shards"),
        ("top",         cmd_top,         "Shards leaderboard"),
        ("pulse",       cmd_pulse,       "Heartbeat + sleep status"),
        ("sleep",       cmd_sleep,       "Put fleet to rest"),
        ("wake",        cmd_wake,        "Wake fleet + run update cycle"),
    ]

    for cmd, handler, _ in CMD_MAP:
        app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    _tasks: list = []

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [BotCommand(cmd, desc) for cmd, _, desc in CMD_MAP]
        )
        _tasks.append(asyncio.ensure_future(pulse.start()))
        _tasks.append(asyncio.ensure_future(_check_wake_file()))
        if offline_sec > 60:
            log.info(f"Running startup wake cycle ({offline_sec/3600:.1f}h offline)")
            asyncio.ensure_future(_wake_cycle(offline_sec))
        log.info(f"Fleet online — gen:{skills.generation}  mem:{len(cortex.nodes)}")

    async def post_shutdown(application: Application) -> None:
        pulse.stop()
        for t in _tasks:
            t.cancel()
        _save()
        log.info("Fleet shutdown — state saved")

    app.post_init     = post_init
    app.post_shutdown = post_shutdown
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
