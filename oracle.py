# ── oracle.py — Venice AI reasoning: private, uncensored, zero retention ─────
import json, logging
import aiohttp

log = logging.getLogger("oracle")
_API = "https://api.venice.ai/api/v1/chat/completions"
_MDL = "llama-3.3-70b"


class Oracle:
    def __init__(self, key: str):
        self.key     = key
        self.online  = bool(key)
        self._tokens = 0

    async def _call(self, prompt: str, max_tokens: int = 300,
                    temperature: float = 0.7) -> str:
        if not self.online:
            return "[Oracle offline — set VENICE_API_KEY]"
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    _API,
                    headers={"Authorization": f"Bearer {self.key}",
                             "Content-Type": "application/json"},
                    json={
                        "model":       _MDL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "max_tokens":  max_tokens,
                        "temperature": temperature,
                        "venice_parameters": {"include_venice_system_prompt": False},
                    },
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    data = await r.json()
                    self._tokens += data.get("usage", {}).get("total_tokens", 0)
                    return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log.warning(f"oracle: {e}")
            return f"[Oracle error: {e}]"

    async def reason(self, context: str, question: str) -> str:
        """Free-form reasoning grounded in memory context."""
        prompt = (f"You are an AI agent in a sovereign swarm fleet. Be direct and concise.\n\n"
                  f"Context from memory:\n{context[:600]}\n\n"
                  f"Question: {question}")
        return await self._call(prompt, max_tokens=250)

    async def crystallize(self, text: str) -> dict:
        """Distill raw memory into title, haiku, essence."""
        prompt = (
            f"Crystallize this agent memory into art metadata. "
            f"Return ONLY valid JSON, no markdown:\n"
            f'{{"title":"2-4 word evocative title","haiku":"line1\\nline2\\nline3",'
            f'"essence":"one sentence","keywords":["w1","w2","w3"]}}\n\n'
            f'Memory: "{text[:500]}"'
        )
        raw = await self._call(prompt, max_tokens=200, temperature=0.85)
        raw = raw.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(raw)
        except Exception:
            return {
                "title":    "Echo Fragment",
                "haiku":    "data flows unseen\nthe fleet holds all it has learned\nsilence remembers",
                "essence":  text[:120],
                "keywords": ["fleet", "memory", "echo"],
            }

    async def analyze_market(self, prices: dict, memories: list) -> str:
        """Financial + sentiment analysis with memory grounding."""
        mem_ctx = "\n".join(f"- {m.content[:80]}" for m in memories[:5])
        price_ctx = "\n".join(f"{sym}: ${p:.2f}" for sym, p in prices.items())
        prompt = (
            f"Fleet market analysis. Be concise, actionable, ≤5 sentences.\n\n"
            f"Live prices:\n{price_ctx}\n\n"
            f"Relevant memories:\n{mem_ctx or 'none'}\n\n"
            f"Provide: trend assessment, key risks, one actionable signal."
        )
        return await self._call(prompt, max_tokens=280, temperature=0.6)

    @property
    def stats(self) -> dict:
        return {"model": _MDL, "tokens_used": self._tokens, "online": self.online}
