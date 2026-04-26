import { useEffect, useRef, useState } from "react";
import AgentStream from "./components/AgentStream";
import LeadCard from "./components/LeadCard";

const FALLBACK_API =
  typeof window !== "undefined" && window.location.hostname !== "localhost"
    ? "https://prospector-7oj3.onrender.com"
    : "http://localhost:8000";

const API = import.meta.env.VITE_API_BASE_URL || FALLBACK_API;

function ProspectorLogo() {
  return (
    <svg
      className="prospector-logo"
      viewBox="0 0 96 96"
      role="img"
      aria-label="Prospector"
    >
      <g className="prospector-float">
        <ellipse cx="49" cy="86" rx="18" ry="5" className="logo-shadow" />
        <g className="axe-swing">
          <rect x="57" y="18" width="5" height="34" rx="2.5" className="axe-handle" transform="rotate(28 59.5 35)" />
          <path
            d="M58 16c9-4 16-2 21 4-7 7-15 9-24 7 0-5 1-8 3-11Z"
            className="axe-head"
            transform="rotate(28 59.5 35)"
          />
        </g>
        <circle cx="47" cy="21" r="9" className="logo-skin" />
        <path d="M36 19c2-10 18-13 24-3-2 1-4 2-6 2-1 0-3 0-5 2-4 2-8 2-13-1Z" className="logo-hat" />
        <path d="M41 31h13c8 0 15 7 15 15v17H27V46c0-8 6-15 14-15Z" className="logo-coat" />
        <path d="M43 31h8l2 8-6 5-6-5 2-8Z" className="logo-shirt" />
        <rect x="43" y="40" width="4" height="20" rx="2" className="logo-strap" />
        <rect x="49" y="40" width="4" height="20" rx="2" className="logo-strap" />
        <path d="M34 45c-5 2-8 6-10 11l8 3c1-3 3-5 7-7l-5-7Z" className="logo-sleeve" />
        <path d="M62 44c5 2 9 6 11 11l-8 4c-1-4-4-6-8-8l5-7Z" className="logo-sleeve" />
        <rect x="35" y="61" width="12" height="18" rx="5" className="logo-pants" />
        <rect x="49" y="61" width="12" height="18" rx="5" className="logo-pants" />
        <rect x="34" y="77" width="14" height="6" rx="3" className="logo-boot" />
        <rect x="48" y="77" width="15" height="6" rx="3" className="logo-boot" />
      </g>
    </svg>
  );
}

export default function App() {
  const [form, setForm] = useState({
    icp: "",
    sender_info: "",
    goal: "Book a 15-minute discovery call",
    max_leads: 1,
  });
  const [running, setRunning] = useState(false);
  const [thoughts, setThoughts] = useState([]);
  const [leads, setLeads] = useState([]);
  const [stats, setStats] = useState(null);
  const bottomRef = useRef(null);

  const loadSavedLeads = async () => {
    const response = await fetch(`${API}/leads`);
    const data = await response.json();
    const mappedLeads = (data.leads || []).map((item) => {
      const enrichment = item.enrichment || {};
      return {
        ...enrichment,
        email: item.email || enrichment.email || "",
        company: item.company || enrichment.company || "",
        company_url: item.company_url || enrichment.company_url || "",
        name: item.contact_name || enrichment.name || "",
        subject: item.email_subject || enrichment.subject || "",
        body: item.email_body || enrichment.body || "",
        total: item.score ?? enrichment.total ?? null,
      };
    });
    setLeads(mappedLeads);
    if (data.stats) {
      setStats(data.stats);
    }
  };

  useEffect(() => {
    loadSavedLeads()
      .catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thoughts]);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const lastLead = leads[0];

  const run = async () => {
    if (!form.icp.trim() || !form.sender_info.trim()) return;

    setRunning(true);
    setThoughts([]);
    setLeads([]);

    try {
      const response = await fetch(`${API}/prospect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, max_leads: Number(form.max_leads || 1) }),
      });

      if (!response.body) {
        throw new Error("No stream returned from backend.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";

        for (const chunk of chunks) {
          const line = chunk
            .split("\n")
            .find((part) => part.startsWith("data: "));
          if (!line) continue;

          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === "thought") {
              setThoughts((prev) => [...prev, { kind: "thought", text: event.content }]);
            } else if (event.type === "tool_call") {
              setThoughts((prev) => [...prev, { kind: "tool", tool: event.tool, args: event.args }]);
            } else if (event.type === "lead") {
              const lead = event.data || event;
              setLeads((prev) => {
                const key = lead.email || `${lead.company}:${lead.name}`;
                const idx = prev.findIndex((l) => (l.email || `${l.company}:${l.name}`) === key);
                if (idx === -1) return [...prev, lead];
                const copy = [...prev];
                copy[idx] = { ...copy[idx], ...lead };
                return copy;
              });
            } else if (event.type === "done") {
              const reason = event.reason ? ` (${event.reason})` : "";
              const metrics = event.metrics
                ? ` | leads=${event.metrics.qualified_leads ?? 0}, searches=${event.metrics.search_calls ?? 0}, enrich=${event.metrics.enrich_calls ?? 0}`
                : "";
              setThoughts((prev) => [
                ...prev,
                { kind: "done", text: `${event.content || "Run complete"}${reason}${metrics}` },
              ]);
            } else if (event.type === "error") {
              setThoughts((prev) => [...prev, { kind: "error", text: event.message || "Unknown error" }]);
            }
          } catch {
            // Ignore malformed stream chunks.
          }
        }
      }
    } catch (err) {
      setThoughts((prev) => [...prev, { kind: "error", text: err.message }]);
    } finally {
      setRunning(false);
      loadSavedLeads().catch(() => {});
    }
  };

  return (
    <div className="app">
      <header>
        <div className="brand">
          <ProspectorLogo />
          <span className="name">PROSPECTOR</span>
          <span className="sub">Autonomous B2B Outreach Agent</span>
        </div>
        {stats && (
          <div className="mem-pill">
            memory: {stats.total_leads} leads | seen {stats.prospects_seen ?? 0} | avg {stats.avg_score}
          </div>
        )}
      </header>

      <div className="layout">
        <aside className="config">
          <div className="config-topline">
            <span className="config-kicker">Demo setup</span>
            <span className="config-status">{running ? "Live run in progress" : "Ready to run"}</span>
          </div>

          <label>TARGET ICP</label>
          <textarea
            rows={7}
            value={form.icp}
            onChange={(e) => setField("icp", e.target.value)}
            placeholder="CTOs at Series A fintech startups using Stripe or Plaid, 10-50 employees"
          />
          <div className="field-note">Be narrow and concrete: vertical, stage, buyer, and trigger.</div>

          <label>SENDER INFO</label>
          <textarea
            rows={6}
            value={form.sender_info}
            onChange={(e) => setField("sender_info", e.target.value)}
            placeholder="PitchFlows - AI cold email agent, 200+ founder users"
          />
          <div className="field-note">Include social proof and one clear outcome metric.</div>

          <label>GOAL</label>
          <input value={form.goal} onChange={(e) => setField("goal", e.target.value)} />
          <div className="field-note">Make the CTA specific: intro call, audit, or pilot.</div>

          <label>MAX LEADS</label>
          <input
            type="number"
            min={1}
            max={10}
            value={form.max_leads}
            onChange={(e) => setField("max_leads", e.target.value)}
          />
          <div className="field-note">For demos, `1` usually performs best.</div>

          <button className={`btn ${running ? "active" : ""}`} onClick={run} disabled={running}>
            {running ? "RUNNING..." : "RUN PROSPECTOR"}
          </button>

          <div className="config-strip">
            <span className="config-chip">quality-first</span>
            <span className="config-chip">memory-aware</span>
            <span className="config-chip">live scoring</span>
          </div>

          <div className="config-summary">
            {lastLead ? (
              <>
                <span className="summary-label">Latest saved lead</span>
                <strong>{lastLead.name || "Contact"} at {lastLead.company || "company"}</strong>
                <span className="summary-meta">score {lastLead.total ?? "--"}/100</span>
              </>
            ) : (
              <>
                <span className="summary-label">Demo hint</span>
                <strong>Use a narrow ICP and let the stream tell the story.</strong>
                <span className="summary-meta">Judges care about business value and a convincing working demo.</span>
              </>
            )}
          </div>
        </aside>

        <section className="stream-panel">
          <div className="panel-head">
            <div className="panel-title">AGENT STREAM</div>
            <div className="panel-meta">{running ? "Streaming live reasoning" : "Reasoning + tool trace"}</div>
          </div>
          <AgentStream thoughts={thoughts} running={running} bottomRef={bottomRef} />
        </section>

        <section className="leads-panel">
          <div className="panel-head">
            <div className="panel-title">LEADS FOUND - {leads.length}</div>
            <div className="panel-meta">{leads.length ? "Qualified and saved" : "Waiting for qualified leads"}</div>
          </div>
          {!leads.length ? <div className="empty">No leads yet.</div> : null}
          {leads.map((lead, idx) => (
            <LeadCard key={`${lead.email || idx}`} lead={lead} />
          ))}
        </section>
      </div>
    </div>
  );
}
