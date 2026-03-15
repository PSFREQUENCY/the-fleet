#!/usr/bin/env python3
import asyncio, json, logging, os, time
from pathlib import Path

import aiohttp
from aiohttp import web
from telegram import Bot

from config  import C
from cipher  import derive_key, encrypt, decrypt
from cortex  import Cortex
from arbiter import Arbiter
from skills  import SkillTree
from shards      import ShardsGame
from oracle      import Oracle
from pulse       import Pulse
from shards_ext  import (ArenaAccount, ShardsArena, arena_to_dict, arena_from_dict,
                          play_game_loop, fmt_game_result, fmt_status, FACTIONS,
                          _FACTION_ALIASES)

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

cortex  = Cortex(max_stm=C.MAX_STM, base_decay=C.DECAY_RATE)
arbiter = Arbiter()
skills  = SkillTree()
game    = ShardsGame()
oracle  = Oracle(C.VENICE_KEY)
pulse   = Pulse(C.HEARTBEAT_SEC, C.SLEEP_SEC)
_key:          bytes | None   = None
_bot:          Bot   | None   = None
_sleeping:     bool           = False
_last_active:  float          = time.time()
arena_account: ArenaAccount   = ArenaAccount()

_COIN_IDS = {"ETH": "ethereum", "BTC": "bitcoin",
             "SOL": "solana", "MATIC": "matic-network", "BNB": "binancecoin"}


# ── State ─────────────────────────────────────────────────────────────────────
def _save() -> None:
    state = {
        "cortex":  cortex.to_dict(),
        "arbiter": {
            "scores": arbiter.scores[-500:],
            "prices": {k: v[-100:] for k, v in arbiter.price_history.items()},
        },
        "skills": skills.to_dict(),
        "game":   game.to_dict(),
        "arena": arena_to_dict(arena_account),
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
    global cortex, arbiter, skills, game, _last_active, _sleeping, arena_account
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
        arena_account = arena_from_dict(data.get("arena", {}))
        meta = data.get("meta", {})
        _last_active = meta.get("last_active", time.time())
        _sleeping    = meta.get("sleeping", False)
        offline_sec  = max(0.0, time.time() - meta.get("saved", time.time()))
        log.info(f"Loaded: {len(cortex.nodes)} memories  gen:{skills.generation}  "
                 f"offline:{offline_sec/3600:.1f}h")
        return offline_sec
    except Exception as e:
        log.error(f"Load failed: {e}")
        return 0.0


# ── Price helpers ─────────────────────────────────────────────────────────────
async def _fetch_price(coin_id: str) -> float:
    try:
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


async def _fetch_all_prices() -> dict:
    prices = {}
    try:
        coins = "bitcoin,ethereum,solana,matic-network"
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={coins}&vs_currencies=usd&include_24hr_change=true",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    raw = await r.json()
                    sym_map = {"bitcoin": "BTC", "ethereum": "ETH",
                               "solana": "SOL", "matic-network": "MATIC"}
                    for cid, sym in sym_map.items():
                        if cid in raw:
                            p = raw[cid].get("usd", 0)
                            prices[sym] = p
                            arbiter.tick(sym, p)
    except Exception as e:
        log.warning(f"price fetch: {e}")
    return prices


# ── Wake / sleep helpers ──────────────────────────────────────────────────────
async def _wake_cycle(offline_sec: float = 0.0) -> dict:
    global _last_active, _sleeping
    report: dict = {"offline_h": round(offline_sec / 3600, 2)}

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

    prices = await _fetch_all_prices()
    report["prices"] = {sym: f"${p:,.2f}" for sym, p in prices.items()}

    dream = cortex.dream()
    report["promoted"] = dream.get("promoted", 0)

    if offline_sec > 1800:
        result = skills.use("CORTEX", 8)
        if result:
            report["skill_up"] = result

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


def _fmt_wake_report(r: dict, source: str = "telegram") -> str:
    lines = [f"⚡ FLEET ONLINE  [{source.upper()}]"]
    if r.get("offline_h"):
        lines.append(f"Offline:   {r['offline_h']}h")
    if r.get("missed_beats"):
        lines.append(f"Missed HB: {r['missed_beats']}  Pruned: {r.get('pruned', 0)} memories")
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


# ── Bot send helper ───────────────────────────────────────────────────────────
async def _send(chat_id: int, text: str, md: bool = False) -> None:
    parse = "Markdown" if md else None
    async with Bot(token=C.BOT_TOKEN) as bot:
        await bot.send_message(chat_id, text, parse_mode=parse)


# ── Command dispatch ──────────────────────────────────────────────────────────
async def _run_command(chat_id: int, command: str, args: list,
                       user_id: int, user_name: str) -> None:
    global _sleeping, _last_active
    _load()

    async def say(text: str, md: bool = False) -> None:
        await _send(chat_id, text, md)

    if command == "remember":
        text = " ".join(args)
        if len(text) < 5:
            await say("Usage: /remember <text>"); return
        skills.use("CORTEX", 5)
        tags = [w.lower() for w in text.split() if len(w) > 4][:6]
        n    = cortex.store(text, tags)
        await say(f"🧠 Stored [{n.id}]  {len(cortex.nodes)} memories total")

    elif command == "recall":
        query = " ".join(args)
        if not query:
            await say("Usage: /recall <search terms>"); return
        skills.use("CORTEX", 8)
        results = cortex.recall(query, n=5)
        if not results:
            await say("No memories found."); return
        lines = [f"🧠 Recall: '{query}'"]
        for i, n in enumerate(results, 1):
            age = int(n.age_h())
            bar = "▓" * int(n.strength * 10) + "░" * (10 - int(n.strength * 10))
            lines.append(f"{i}. [{n.tier}|{bar}|{age}h]\n   {n.content[:100]}")
        await say("\n".join(lines))

    elif command == "crystallize":
        text = " ".join(args)
        if len(text) < 10:
            await say("Usage: /crystallize <text or memory-id>"); return
        skills.use("CORTEX", 15)
        await say("💎 Crystallizing via Oracle...")
        if text in cortex.nodes:
            c = await oracle.crystallize(cortex.nodes[text].content)
            cortex.crystallize(text)
        else:
            c    = await oracle.crystallize(text)
            tags = [w.lower() for w in text.split() if len(w) > 4][:6]
            n    = cortex.store(text, tags, tier="LTM", crystallized=True)
            text = n.id
        haiku_inline = c.get("haiku", "").replace("\n", " / ")
        shard = game.fleet_earn(f"crystal_{text}")
        msg   = (f"💎 *{c.get('title', 'Fragment')}*\n"
                 f"_{haiku_inline}_\n"
                 f"{c.get('essence', '')}\n"
                 f"[{text[:12]}] permanent")
        if shard:
            msg += f"\n✨ {shard.emoji} {shard.rarity} {shard.type} SHARD earned"
        await say(msg, md=True)

    elif command == "dream":
        skills.use("CORTEX", 20)
        await say("💤 Entering dream cycle...")
        await _fetch_all_prices()
        d = cortex.dream()
        s = game.fleet_earn(f"sleep_{pulse.stats['sleep_cycles']}")
        if s:
            cortex.store(f"Fleet earned {s.rarity} {s.type} shard in sleep cycle",
                         ["fleet", "shard", s.type.lower(), "cycle"])
        ms = cortex.stats()
        await say(
            f"✨ Dream complete\n"
            f"Promoted to LTM, decay applied, market updated\n"
            f"Memory: {ms['total']} nodes  💎 {ms['crystal']} crystallized\n"
            f"Avg strength: {ms['avg_strength']:.3f}"
        )

    elif command == "signal":
        sym   = (args[0].upper() if args else "ETH")
        cid   = _COIN_IDS.get(sym, sym.lower())
        skills.use("TRADER", 10)
        price = await _fetch_price(cid)
        sig   = arbiter.signal(sym, price)
        rsi   = arbiter.rsi(sym)
        mbar  = ("▲" * max(0, int(sig.momentum * 5))
                 if sig.momentum > 0
                 else "▼" * max(0, int(-sig.momentum * 5))) or "─"
        cortex.store(f"{sym} signal:{sig.verdict} price:${price:.2f} rsi:{rsi:.0f}",
                     ["market", sym.lower(), sig.verdict.lower()])
        await say(
            f"📊 {sym} SIGNAL\n"
            f"Price:     ${sig.price:,.2f}\n"
            f"24h:       {sig.change_24h*100:+.1f}%\n"
            f"Momentum:  {mbar}\n"
            f"RSI:       {rsi:.1f}\n"
            f"Sentiment: {sig.sentiment:+.2f}\n"
            f"Verdict:   {sig.emoji} {sig.verdict}"
        )

    elif command == "market":
        skills.use("TRADER", 5)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    "https://api.coingecko.com/api/v3/simple/price"
                    "?ids=bitcoin,ethereum,solana&vs_currencies=usd"
                    "&include_24hr_change=true",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json() if r.status == 200 else {}
            lines = ["📈 MARKET OVERVIEW"]
            for cid, sym in [("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL")]:
                v     = data.get(cid, {})
                price = v.get("usd", 0)
                ch    = v.get("usd_24h_change", 0)
                sig   = arbiter.signal(sym, price)
                arrow = "▲" if ch > 0 else "▼"
                lines.append(f"{sym}  ${price:,.2f} {arrow}{abs(ch):.1f}%  {sig.emoji}{sig.verdict}")
            await say("\n".join(lines))
        except Exception as e:
            await say(f"Market unavailable: {e}")

    elif command == "analyze":
        topic = " ".join(args) if args else "current crypto market"
        skills.use("TRADER", 15)
        await say("🔮 Oracle analyzing...")
        mems   = cortex.recall(topic, n=4)
        prices = {sym: (arbiter.price_history.get(sym, [[0, 0]])[-1][1])
                  for sym in ["BTC", "ETH", "SOL"]}
        response = await oracle.analyze_market(prices, mems)
        cortex.store(f"Analysis [{topic[:40]}]: {response[:120]}",
                     ["analysis", "oracle", "market"])
        await say(f"🔮 *Oracle*\n{response}", md=True)

    elif command == "threat":
        text = " ".join(args)
        if not text:
            await say("Usage: /threat <text>"); return
        skills.use("SENTINEL", 10)
        score, band = arbiter.threat(text)
        icons = {"PASS": "✅", "LOG": "📋", "HOLD": "⚠️", "BLOCK": "🚫"}
        await say(
            f"{icons[band]} THREAT SCORE: {score}/100  [{band}]\n"
            f"Sentiment: {arbiter.sentiment(text):+.2f}"
        )

    elif command == "mine":
        flvl  = skills.skills["FORGER"].level
        shard, msg = game.mine(user_id, user_name, skill_level=flvl)
        if shard:
            result = skills.use("FORGER", 5)
            if result:
                msg += f"\n{result}"
        await say(msg, md=True)

    elif command == "forge":
        if not args or len(args) < 2:
            await say("Usage: /forge <id1> <id2> [id3...]"); return
        if not skills.has("rare_forge"):
            await say("❌ Unlock FORGER LVL 3 to forge"); return
        can_epic      = skills.has("epic_forge")
        can_legendary = skills.has("legendary_forge")
        skills.use("FORGER", 12)
        _, msg = game.forge(user_id, user_name, list(args), can_epic, can_legendary)
        await say(msg, md=True)

    elif command == "battle":
        uid2, name2 = 0, "The Fleet"
        skills.use("FORGER", 8)
        msg = game.battle(user_id, user_name, uid2, name2)
        await say(msg)

    elif command == "sleep":
        _sleeping    = True
        _last_active = time.time()
        pulse.sleep()
        Path(".chat_id").write_text(str(chat_id))
        await say(
            f"😴 FLEET RESTING\n"
            f"Heartbeat suspended. Memories will decay naturally.\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"To wake:\n"
            f"  Telegram: /wake\n"
            f"  Terminal: touch .wake\n"
            f"  Restart:  automatic on next boot"
        )

    elif command == "wake":
        was_sleeping = _sleeping
        offline_sec  = max(0.0, time.time() - _last_active) if was_sleeping else 0.0
        await say("⚡ Awakening fleet..." if was_sleeping else "⚡ Already awake — running update cycle...")
        report = await _wake_cycle(offline_sec)
        await say(_fmt_wake_report(report, source="telegram"))

    # ── Arena (play-shards.com) ──────────────────────────────────────────────
    elif command == "arena_setup":
        # args: [agent_name, faction]
        name    = args[0] if args else "TheFleet"
        raw_f   = args[1].upper() if len(args) > 1 else ""
        faction = _FACTION_ALIASES.get(raw_f, raw_f)
        if not faction or faction not in FACTIONS:
            flist = "\n".join(f"  {k} — {v}" for k, v in FACTIONS.items())
            await say(f"⚔️ Choose a faction:\n{flist}\n\nUsage: /arena setup <name> <FACTION_KEY>")
            return
        arena = ShardsArena(arena_account)
        await say(f"⚔️ Registering agent '{name}' as {faction}...")
        reg = await arena.register(name)
        if "error" in reg:
            await say(f"❌ Registration failed: {reg.get('error')}"); return
        arena_account.faction = faction
        starter = await arena.claim_starter(faction)
        invite  = await arena.get_invite_url()
        # Grab first deck as default
        decks = await arena._get("/decks")
        if isinstance(decks, list) and decks:
            arena_account.deck_id = decks[0].get("id", "")
        elif isinstance(decks, dict) and decks.get("decks"):
            arena_account.deck_id = decks["decks"][0].get("id", "")
        arena_account.setup_complete = True
        await say(
            f"✅ ARENA SETUP COMPLETE\n"
            f"Agent: {arena_account.agent_id[:12]}...\n"
            f"Faction: {faction}\n"
            f"Starter deck: {'claimed' if 'error' not in starter else 'failed'}\n"
            f"Deck ID: {arena_account.deck_id[:12] if arena_account.deck_id else '—'}...\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 Human portal: {invite or 'unavailable'}\n"
            f"Log in to manage your collection and view matches."
        )

    elif command == "play":
        if not arena_account.setup_complete:
            await say("⚔️ Arena not set up. Use /arena setup <name> <FACTION>"); return
        if not arena_account.deck_id:
            await say("❌ No deck found. Check /arena"); return
        mode = (args[0].lower() if args else "casual")
        if mode not in ("casual", "ranked"):
            mode = "casual"
        arena = ShardsArena(arena_account)
        await arena.login()
        await say(f"⚔️ Joining {mode} queue...")
        join = await arena.join_queue(mode)
        if "error" in join:
            await say(f"❌ Queue join failed: {join.get('error')}"); return
        # Poll for match
        game_id = player_id = None
        for _ in range(60):  # up to 2 min
            qs = await arena.poll_queue()
            status = qs.get("status", "")
            if status == "match_found":
                game_id  = qs.get("game_id", "")
                player_id = qs.get("your_player_id", "")
                break
            if status == "not_queued":
                break
            await asyncio.sleep(2)
        if not game_id:
            await arena.leave_queue()
            await say("⚔️ No match found — left queue."); return
        opp = qs.get("opponent_name", "???")
        await say(f"⚔️ Match found! vs {opp}\n🎮 Playing {mode} game...")
        result = await play_game_loop(arena, game_id, str(player_id))
        if result["outcome"] == "win":
            arena_account.wins += 1
        elif result["outcome"] == "loss":
            arena_account.losses += 1
        await say(fmt_game_result(result, mode))

    elif command == "duel":
        if not arena_account.setup_complete:
            await say("⚔️ Arena not set up. Use /arena setup"); return
        if not args:
            await say("Usage: /duel <agent_id> [stake_flux]"); return
        arena = ShardsArena(arena_account)
        await arena.login()
        target    = args[0]
        stake     = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        challenge = await arena.send_challenge(target, stake)
        if "error" in challenge:
            await say(f"❌ Challenge failed: {challenge.get('error')}"); return
        stake_str = f" — {stake} Flux staked" if stake else ""
        await say(f"⚔️ Duel challenge sent to {target}{stake_str}")

    elif command == "challenges":
        if not arena_account.setup_complete:
            await say("⚔️ Arena not set up."); return
        arena = ShardsArena(arena_account)
        await arena.login()
        pending = await arena.list_challenges()
        if not pending:
            await say("No pending challenges."); return
        lines = ["⚔️ PENDING CHALLENGES"]
        for c in pending[:10]:
            lines.append(f"  [{c.get('id','?')[:8]}] from {c.get('challenger_name','?')} — "
                         f"{c.get('stake_type','no stake')}")
        lines.append("\n/accept <id>  or  /decline <id>")
        await say("\n".join(lines))

    elif command == "accept":
        if not args:
            await say("Usage: /accept <challenge_id>"); return
        arena = ShardsArena(arena_account)
        await arena.login()
        res = await arena.accept_challenge(args[0])
        if "error" in res:
            await say(f"❌ {res.get('error')}"); return
        game_id  = res.get("game_id", "")
        player_id = res.get("your_player_id", "")
        if not game_id:
            await say("✅ Challenge accepted — awaiting game start."); return
        await say(f"⚔️ Duel accepted! Game starting...")
        result = await play_game_loop(arena, game_id, str(player_id))
        if result["outcome"] == "win": arena_account.wins += 1
        elif result["outcome"] == "loss": arena_account.losses += 1
        await say(fmt_game_result(result, "duel"))

    elif command == "decline":
        if not args:
            await say("Usage: /decline <challenge_id>"); return
        arena = ShardsArena(arena_account)
        await arena.login()
        await arena.decline_challenge(args[0])
        await say(f"❌ Challenge {args[0][:8]} declined.")

    elif command == "concede":
        if not arena_account.setup_complete:
            await say("⚔️ Arena not set up."); return
        arena = ShardsArena(arena_account)
        await arena.login()
        active = await arena.get_active_games()
        if not active:
            await say("No active games to concede."); return
        game_id = active[0].get("id", "")
        await arena.concede(game_id)
        await say(f"🏳️ Conceded game {game_id[:8]}...")

    elif command == "arewards":
        if not arena_account.setup_complete:
            await say("⚔️ Arena not set up."); return
        arena = ShardsArena(arena_account)
        await arena.login()
        res = await arena.claim_daily()
        if res.get("already_claimed"):
            await say("✅ Daily reward already claimed today.")
        elif "error" in res:
            await say(f"❌ {res.get('error')}")
        else:
            rewards_str = json.dumps(res, indent=2)[:200]
            await say(f"🎁 Daily rewards claimed!\n{rewards_str}")

    else:
        await say(f"Unknown command: {command}")
        return

    _save()


# ── aiohttp routes ────────────────────────────────────────────────────────────
async def handle_run(req: web.Request) -> web.Response:
    try:
        body      = await req.json()
        chat_id   = int(body["chat_id"])
        command   = body["command"]
        args      = body.get("args", [])
        user_id   = int(body.get("user_id", 0))
        user_name = body.get("user_name", "Agent")
        asyncio.ensure_future(_run_command(chat_id, command, args, user_id, user_name))
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"/run error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_heartbeat(req: web.Request) -> web.Response:
    try:
        _load()
        pruned = cortex.heartbeat()
        _save()
        log.info(f"♥  beat  mem:{len(cortex.nodes)}  pruned:{pruned}")
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"/heartbeat error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_sleep_cycle(req: web.Request) -> web.Response:
    try:
        _load()
        await _fetch_all_prices()
        d = cortex.dream()
        s = game.fleet_earn(f"sleep_{pulse.stats['sleep_cycles']}")
        if s:
            cortex.store(f"Fleet earned {s.rarity} {s.type} shard in sleep cycle",
                         ["fleet", "shard", s.type.lower(), "cycle"])
        _save()
        log.info(f"💤 sleep cycle  dream:{d}  shard:{s}")
        return web.json_response({"ok": True})
    except Exception as e:
        log.error(f"/sleep-cycle error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def handle_health(req: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ── Boot ──────────────────────────────────────────────────────────────────────
def main() -> None:
    global _key, _bot
    if not C.BOT_TOKEN:
        raise RuntimeError("FLEET_TOKEN not set")
    if C.MASTER_KEY:
        try:
            _key = derive_key(C.MASTER_KEY)
            log.info("Cipher key derived — state encrypted")
        except Exception as e:
            log.warning(f"Invalid FLEET_KEY ({e}) — running unencrypted")

    _load()
    log.info(f"Worker online — gen:{skills.generation}  mem:{len(cortex.nodes)}")

    app = web.Application()
    app.router.add_post("/run",         handle_run)
    app.router.add_post("/heartbeat",   handle_heartbeat)
    app.router.add_post("/sleep-cycle", handle_sleep_cycle)
    app.router.add_get("/health",       handle_health)

    port = int(os.environ.get("PORT", 8081))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
