import React, { useState } from "react";

export default function LeadCard({ lead }) {
  const [open, setOpen] = useState(false);
  const score = lead.total ?? null;
  const companyUrl = lead.company_url || "";

  return (
    <div className="card">
      <div className="card-head" onClick={() => setOpen((v) => !v)}>
        <div>
          <div className="c-name">{lead.name || "Contact"}</div>
          <div className="c-meta">
            {(lead.title || "Unknown title") + " - " + (lead.company || "Unknown company")}
          </div>
          {lead.signals?.length > 0 && (
            <div className="tags">
              {lead.signals.map((tag) => (
                <span className="tag" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
        {score !== null && (
          <div className="score">
            {score}
            <span>/100</span>
          </div>
        )}
      </div>

      {open && (
        <div className="card-body">
          <div className="subj">📨 {lead.subject || "(no subject)"}</div>
          <div className="body-text">{lead.body || "(no body)"}</div>
          {lead.email && <div className="email-addr">{lead.email}</div>}
          {lead.bucket && <div className="bucket">Bucket: {lead.bucket}</div>}
          {companyUrl && (
            <a className="company-link" href={companyUrl} target="_blank" rel="noreferrer">
              Visit company site
            </a>
          )}
        </div>
      )}
    </div>
  );
}
