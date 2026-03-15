#!/usr/bin/env python3
import asyncio, json, logging, os, time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from config      import C
from cipher      import derive_key, decrypt
from cortex      import Cortex
from arbiter     import Arbiter
from skills      import SkillTree
from shards      import ShardsGame
from oracle      import Oracle
from pulse       import Pulse
from shards_ext  import ArenaAccount, arena_from_dict, fmt_status, FACTIONS

log = logging.getLogger("herald")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

WORKER_URL = os.environ.get("WORKER_URL", "http://localhost:8081")

cortex  = Cortex(max_stm=C.MAX_STM, base_decay=C.DECAY_RATE)
arbiter = Arbiter()
skills  = SkillTree()
game    = ShardsGame()
oracle  = Oracle(C.VENICE_KEY)
pulse   = Pulse(C.HEARTBEAT_SEC, C.SLEEP_SEC)
_key:          bytes | None  = None
arena_account: ArenaAccount  = ArenaAccount()


# ── State ─────────────────────────────────────────────────────────────────────
def _load() -> float:
    global cortex, arbiter, skills, game, arena_account
    p = Path(C.DB_PATH)
    if not p.exists():
        return 0.0
    try:
        raw  = p.read_bytes()
        data = json.loads(decrypt(_key, raw) if _key else raw)
        cortex  = Cortex.from_dict(data.get("cortex", {}), C.MAX_STM, C.DECAY_RATE)
        skills  = SkillTree.from_dict(data.get("skills", {}))
        game    = ShardsGame.from_dict(data.get("game", {}))
        arena_account = arena_from_dict(data.get("arena", {}))
        ab = data.get("arbiter", {})
        arbiter.scores        = ab.get("scores", [])
        arbiter.price_history = ab.get("prices", {})
        meta = data.get("meta", {})
        offline_sec = max(0.0, time.time() - meta.get("saved", time.time()))
        log.info(f"Loaded: {len(cortex.nodes)} memories  gen:{skills.generation}  "
                 f"offline:{offline_sec/3600:.1f}h")
        return offline_sec
    except Exception as e:
        log.error(f"Load failed: {e}")
        return 0.0


def _refresh() -> None:
    _load()


# ── Worker dispatch ────────────────────────────────────────────────────────────
async def _id_token(audience: str) -> str:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://metadata.google.internal/computeMetadata/v1/instance/"
                f"service-accounts/default/identity?audience={audience}",
                headers={"Metadata-Flavor": "Google"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                return await r.text()
    except Exception:
        return ""


async def _worker_post(path: str, body: dict = None) -> None:
    token = await _id_token(WORKER_URL)
    hdrs  = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession() as sess:
            await sess.post(f"{WORKER_URL}{path}", json=body or {},
                            headers=hdrs, timeout=aiohttp.ClientTimeout(total=30))
    except Exception as e:
        log.warning(f"worker {path}: {e}")


async def _dispatch(chat_id: int, command: str, args: list,
                    user_id: int, user_name: str) -> None:
    await _worker_post("/run", {"chat_id": chat_id, "command": command,
                                "args": args, "user_id": user_id,
                                "user_name": user_name})


_SKILL_LABELS = {
    "remember": "CORTEX", "recall": "CORTEX", "crystallize": "CORTEX", "dream": "CORTEX",
    "signal": "TRADER", "market": "TRADER", "analyze": "TRADER",
    "threat": "SENTINEL",
    "mine": "FORGER", "forge": "FORGER", "battle": "FORGER",
    "sleep": "SYSTEM", "wake": "SYSTEM",
    "arena_setup": "HERALD", "play": "HERALD", "duel": "HERALD",
    "challenges": "HERALD", "accept": "HERALD", "decline": "HERALD",
    "concede": "HERALD", "leave": "HERALD", "arewards": "HERALD",
}


def _heavy(command: str):
    async def handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        _save_chat_id(upd)
        _refresh()
        skill = _SKILL_LABELS.get(command, command.upper())
        await upd.message.reply_text(f"⚡ routing → {skill}...")
        asyncio.ensure_future(_dispatch(
            upd.effective_chat.id, command,
            ctx.args or [],
            upd.effective_user.id,
            upd.effective_user.first_name or "Agent",
        ))
    return handler


# ── Pulse → worker fire-and-forget ────────────────────────────────────────────
async def _hb_dispatch() -> None:
    await _worker_post("/heartbeat")


async def _sleep_dispatch() -> None:
    await _worker_post("/sleep-cycle")


# ── Chat ID capture ────────────────────────────────────────────────────────────
def _save_chat_id(upd: Update) -> None:
    if upd and upd.effective_chat:
        Path(".chat_id").write_text(str(upd.effective_chat.id))


# ── Fast commands ──────────────────────────────────────────────────────────────
async def cmd_start(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    skills.use("HERALD", 5)
    await upd.message.reply_text(
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
    _save_chat_id(upd); _refresh()
    skills.use("HERALD", 3)
    ms = cortex.stats()
    ab = arbiter.stats()
    ps = pulse.stats
    top = " ".join(f"#{t}" for t, _ in ms["top_tags"])
    await upd.message.reply_text(
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


async def cmd_pulse(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd)
    ps = pulse.stats
    mode = "😴 SLEEPING" if ps["sleeping"] else "⚡ ACTIVE"
    await upd.message.reply_text(
        f"♥  PULSE  [{mode}]\n"
        f"Beats:      {ps['beats']}\n"
        f"Sleep:      {ps['sleep_cycles']}\n"
        f"Next HB:    {ps['next_hb_in']}s\n"
        f"Next sleep: {ps['next_sleep_in']}s"
    )


async def cmd_shards(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    uid  = upd.effective_user.id
    name = upd.effective_user.first_name or "Agent"
    p    = game.player(uid, name)
    if not p.shards:
        await upd.message.reply_text("No shards. Use /mine to find some."); return
    top   = sorted(p.shards.values(), key=lambda s: s.value, reverse=True)[:10]
    lines = [f"💎 {name} — {len(p.shards)} shards  {p.score:.0f}pts  {p.wins}W/{p.losses}L",
             f"Total power: {p.total_power():.0f}"]
    for s in top:
        lines.append(s.describe())
    await upd.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_top(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    await upd.message.reply_text(game.leaderboard())


async def cmd_skills(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    await upd.message.reply_text(f"```\n{skills.render()}\n```", parse_mode="Markdown")


async def cmd_arena(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    if not arena_account.setup_complete:
        flist = "\n".join(f"  {k}" for k in FACTIONS)
        await upd.message.reply_text(
            f"⚔️ ARENA — play-shards.com\n"
            f"Not set up yet.\n\n"
            f"To begin: /arena_setup <agent_name> <FACTION>\n\nFactions:\n{flist}"
        )
        return
    await upd.message.reply_text(fmt_status({}, arena_account))


async def cmd_evolve(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _save_chat_id(upd); _refresh()
    tl     = skills.total_levels()
    next_t = skills.generation * 14
    prog   = min(1.0, tl / next_t)
    bar    = "█" * int(prog * 20) + "░" * (20 - int(prog * 20))
    recent = "\n".join(skills.log[-5:]) if skills.log else "No unlocks yet"
    await upd.message.reply_text(
        f"🧬 EVOLUTION\n"
        f"Generation:   {skills.generation}\n"
        f"Skill levels: {tl}/{next_t}\n"
        f"[{bar}] {prog*100:.0f}%\n"
        f"EVO score: {skills.evo_score:.1f}  POWER: {skills.power():.0f}\n\n"
        f"Recent unlocks:\n{recent}"
    )


# ── Passive messages ──────────────────────────────────────────────────────────
async def on_message(upd: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not upd.message or not upd.message.text:
        return
    _save_chat_id(upd)
    text = upd.message.text
    score, band = arbiter.threat(text)
    if band == "BLOCK":
        await upd.message.reply_text(f"🚫 THREAT DETECTED [{score}/100] — flagged")
        skills.use("SENTINEL", 5)


# ── Boot ──────────────────────────────────────────────────────────────────────
def main() -> None:
    global _key
    if not C.BOT_TOKEN:
        raise RuntimeError("FLEET_TOKEN not set")
    if C.MASTER_KEY:
        try:
            _key = derive_key(C.MASTER_KEY)
            log.info("Cipher key derived — state encrypted")
        except Exception as e:
            log.warning(f"Invalid FLEET_KEY ({e}) — running unencrypted")

    _load()

    pulse.on_heartbeat(_hb_dispatch)
    pulse.on_sleep(_sleep_dispatch)

    app = Application.builder().token(C.BOT_TOKEN).build()

    FAST_CMDS = [
        ("start",       cmd_start,   "Wake the fleet"),
        ("status",      cmd_status,  "Fleet status"),
        ("pulse",       cmd_pulse,   "Heartbeat + sleep status"),
        ("shards",      cmd_shards,  "View your shards"),
        ("top",         cmd_top,     "Shards leaderboard"),
        ("skills",      cmd_skills,  "View skill tree"),
        ("evolve",      cmd_evolve,  "Evolution progress"),
        ("arena",       cmd_arena,   "Arena status (play-shards.com)"),
    ]
    HEAVY_CMDS = [
        ("remember",    "Store a memory"),
        ("recall",      "Search memories"),
        ("crystallize", "Crystallize memory via Oracle"),
        ("dream",       "Deep memory consolidation"),
        ("signal",      "Trading signal [sym]"),
        ("market",      "Live market overview"),
        ("analyze",     "Oracle market analysis"),
        ("threat",      "Threat score text"),
        ("mine",        "Mine for shards"),
        ("forge",       "Forge shards together"),
        ("battle",      "Battle for shards"),
        ("sleep",       "Put fleet to rest"),
        ("wake",        "Wake fleet + run update cycle"),
        # Arena commands
        ("arena_setup", "Setup arena agent [name] [faction]"),
        ("play",        "Play arena game [casual|ranked]"),
        ("duel",        "Send duel challenge [agent_id]"),
        ("challenges",  "List pending duel challenges"),
        ("accept",      "Accept challenge [id]"),
        ("decline",     "Decline challenge [id]"),
        ("concede",     "Concede active arena game"),
        ("leave",       "Leave matchmaking queue"),
        ("arewards",    "Claim arena daily rewards"),
    ]

    for cmd, handler, _ in FAST_CMDS:
        app.add_handler(CommandHandler(cmd, handler))
    for cmd, _ in HEAVY_CMDS:
        app.add_handler(CommandHandler(cmd, _heavy(cmd)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    all_cmds = [(c, d) for c, _, d in FAST_CMDS] + list(HEAVY_CMDS)
    _tasks: list = []

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(
            [BotCommand(c, d) for c, d in all_cmds]
        )
        _tasks.append(asyncio.ensure_future(pulse.start()))
        log.info(f"Herald online — gen:{skills.generation}  mem:{len(cortex.nodes)}")

    async def post_shutdown(application: Application) -> None:
        pulse.stop()
        for t in _tasks:
            t.cancel()
        log.info("Herald shutdown")

    app.post_init     = post_init
    app.post_shutdown = post_shutdown

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    if webhook_url:
        port    = int(os.environ.get("PORT", 8080))
        url_path = urlparse(webhook_url).path or "/"
        secret   = os.environ.get("WEBHOOK_SECRET", "")
        log.info(f"Webhook mode — {webhook_url}  port:{port}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=url_path,
            webhook_url=webhook_url,
            **({"secret_token": secret} if secret else {}),
            drop_pending_updates=True,
        )
    else:
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
