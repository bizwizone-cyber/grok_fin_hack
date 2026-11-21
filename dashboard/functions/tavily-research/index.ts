// Edge function: analyst-support web research via Tavily. NEVER used by system/run or the screening decision.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { ...CORS, "Content-Type": "application/json" } });

const URL_ = Deno.env.get("SUPABASE_URL")!;
const SRK = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const H = { apikey: SRK, Authorization: `Bearer ${SRK}`, "Content-Type": "application/json" };

async function rest(path: string, init: RequestInit = {}) {
  const r = await fetch(`${URL_}/rest/v1/${path}`, { ...init, headers: { ...H, ...(init.headers || {}) } });
  const t = await r.text();
  if (!r.ok) throw new Error(`db ${r.status}: ${t.slice(0, 200)}`);
  return t ? JSON.parse(t) : null;
}

async function tavilyKey(): Promise<string | null> {
  const e = Deno.env.get("TAVILY_API_KEY");
  if (e) return e;
  const rows = await rest("app_secrets?select=value&name=eq.TAVILY_API_KEY&limit=1");
  return rows && rows[0] ? rows[0].value : null;
}

// Pull the listed (watchlist) name and countries out of our own reason text.
function parse(reason: string) {
  let name = "";
  let m = reason.match(/the listed profile is (.+?)\.?\s*$/i);
  if (m) name = m[1];
  if (!name) {
    m = reason.match(/hit's name (.+?)(?:,? and the |,? and |\. |\.$|$)/i);
    if (m) name = m[1];
  }
  name = name.replace(/\((clone|alias)[^)]*\)/ig, "").trim();
  if (/^[^,]+,\s*[^,]+$/.test(name)) { const [a, b] = name.split(","); name = `${b.trim()} ${a.trim()}`; }
  let countries = "";
  m = reason.match(/hit is linked (?:only )?to ([^.]+?)(?:\.|$)/i) || reason.match(/linked to ([^.]+?)(?:\.|$)/i);
  if (m) countries = m[1].split(/,| and /).map((s) => s.trim()).filter(Boolean).slice(0, 3).join(" ");
  return { name, countries };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  try {
    const body = await req.json().catch(() => ({}));
    const run_id = String(body.run_id || ""), hit_id = String(body.hit_id || "");
    if (!/^[0-9a-f-]{36}$/i.test(run_id) || !/^[A-Za-z0-9_-]{1,40}$/.test(hit_id)) return json({ error: "run_id and hit_id required" }, 400);

    const dec = await rest(`decisions?select=reason,reason_code&run_id=eq.${run_id}&hit_id=eq.${encodeURIComponent(hit_id)}&limit=1`);
    if (!dec || !dec[0]) return json({ error: "unknown hit" }, 404);

    // Rate limits: same hit once per 60 s; 30 researches per minute overall (protects the Tavily quota).
    const since = new Date(Date.now() - 60_000).toISOString();
    const recentHit = await rest(`web_research?select=id&run_id=eq.${run_id}&hit_id=eq.${encodeURIComponent(hit_id)}&created_at=gte.${encodeURIComponent(since)}&limit=1`);
    if (recentHit && recentHit.length) return json({ error: "This hit was researched in the last 60 seconds – please wait." }, 429);
    const recentAll = await rest(`web_research?select=id&created_at=gte.${encodeURIComponent(since)}&limit=30`);
    if (recentAll && recentAll.length >= 30) return json({ error: "Too many research requests – try again in a minute." }, 429);

    const { name, countries } = parse(dec[0].reason || "");
    if (!name) return json({ error: "Could not find the listed name in this hit's reason text." }, 422);
    const query = `${name} ${countries} sanctions OR PEP OR fraud`.replace(/\s+/g, " ").trim();

    const key = await tavilyKey();
    if (!key) return json({ error: "Tavily API key not configured yet." }, 503);
    const tr = await fetch("https://api.tavily.com/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ query, search_depth: "basic", include_answer: true, max_results: Math.min(5, 5) }),
    });
    const tt = await tr.text();
    if (!tr.ok) return json({ error: `Tavily error ${tr.status}`, detail: tt.slice(0, 200) }, 502);
    const tj = JSON.parse(tt);
    const results = (tj.results || []).slice(0, 5).map((r: any) => ({
      title: String(r.title || "").slice(0, 200),
      url: String(r.url || ""),
      content: String(r.content || "").slice(0, 400),
    }));
    const answer = tj.answer ? String(tj.answer).slice(0, 2000) : "";
    const saved = await rest("web_research", {
      method: "POST",
      headers: { Prefer: "return=representation" },
      body: JSON.stringify({ run_id, hit_id, query, results, summary: answer }),
    });
    return json({ query, answer, results, created_at: saved && saved[0] ? saved[0].created_at : new Date().toISOString() });
  } catch (e) {
    return json({ error: String((e as Error).message || e) }, 500);
  }
});
