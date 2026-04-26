import React from "react";

const TOOL_META = {
  web_search: { icon: "🔎", label: "Searching web" },
  enrich_contact: { icon: "📈", label: "Enriching contact" },
  generate_email: { icon: "📨", label: "Drafting email" },
  score_email: { icon: "⭐️", label: "Scoring email" },
};

export default function AgentStream({ thoughts, running, bottomRef }) {
  if (!thoughts.length && !running) {
    return <div className="empty">Run Prospector to see live agent reasoning and tool calls.</div>;
  }

  return (
    <div className="stream">
      {thoughts.map((item, idx) => {
        if (item.kind === "tool") {
          const meta = TOOL_META[item.tool] || { icon: "🧠", label: item.tool };
          return (
            <div className="line" key={idx}>
              <div className="tool-row">
                <span className="t-icon">{meta.icon}</span>
                <span className="t-label">{meta.label}</span>
                <span className="t-args">{JSON.stringify(item.args || {})}</span>
              </div>
            </div>
          );
        }

        if (item.kind === "error") {
          return (
            <div className="line" key={idx}>
              <div className="err">{item.text}</div>
            </div>
          );
        }

        if (item.kind === "done") {
          return (
            <div className="line" key={idx}>
              <div className="t-label">{item.text}</div>
            </div>
          );
        }

        return (
          <div className="line thought" key={idx}>
            <p>{item.text}</p>
          </div>
        );
      })}
      {running && <div className="pulse">Prospector running...</div>}
      <div ref={bottomRef} />
    </div>
  );
}
