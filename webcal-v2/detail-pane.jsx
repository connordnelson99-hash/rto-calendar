// detail-pane.jsx — meeting detail drawer + doc reader.
// All synthetic content removed. AI summary / hydro flag come from
// the screening pipeline (rto_events_with_docs.json) and render only
// when present.

// PJM stores status with normal-case spaces (e.g. "evaluation in progress",
// "awaiting senior committee approval"). Sentence-case for display, with a
// couple of explicit shortenings for the long ones. CAISO already uses
// title-case ("Active", "Completed", "Closed") and passes through.
const STATUS_LABEL_OVERRIDES = {
  "awaiting implementation & ferc approval": "Awaiting FERC approval",
  "awaiting senior committee approval":      "Awaiting senior committee approval",
  "solution alternatives proposed":          "Solution alternatives proposed",
  "evaluation in progress":                  "Evaluation in progress",
  "on-hold":                                 "On hold",
  "closed":                                  "Closed",
  "canceled":                                "Canceled",
  "active":                                  "Active",
  "completed":                               "Completed",
};
function formatStatus(s) {
  if (!s) return "Status unknown";
  const k = String(s).trim().toLowerCase();
  return STATUS_LABEL_OVERRIDES[k] || (k.charAt(0).toUpperCase() + k.slice(1));
}

function formatShortDate(iso) {
  if (!iso) return null;
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// CAISO bundles many dated events per stage as a single multi-line string
// like "April 16, 2026 Configurable Parameters working group\nApril 30, 2026
// Comments due". Split into [{date, label}] pairs for vertical rendering.
const CAISO_DATE_RE = /^([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4}\*?)\s+(.*)$/;
function parseCaisoStage(raw) {
  if (!raw || raw === "Completed") return [];
  const out = [];
  for (const line of String(raw).split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    const m = s.match(CAISO_DATE_RE);
    if (m) out.push({ date: m[1], label: m[2] });
    else   out.push({ date: null, label: s });
  }
  return out;
}

// CAISO timeline: vertical list of Stage A-D, each holding 0-N dated events.
const CaisoTimeline = ({ issue }) => {
  const stages = [
    { key: "A", events: parseCaisoStage(issue.stage_a) },
    { key: "B", events: parseCaisoStage(issue.stage_b) },
    { key: "C", events: parseCaisoStage(issue.stage_c) },
    { key: "D", events: parseCaisoStage(issue.stage_d) },
  ];
  const isCompleted = issue.stage_d === "Completed" || issue.status === "Completed";
  const populated = stages.filter(s => s.events.length > 0);
  if (!populated.length && !isCompleted) return null;

  return (
    <div className="caiso-timeline">
      {populated.map(s => (
        <div key={s.key} className="caiso-stage">
          <span className="caiso-stage-marker">{s.key}</span>
          <div className="caiso-stage-events">
            {s.events.map((ev, i) => (
              <div key={i} className="caiso-stage-event">
                {ev.date && <span className="caiso-stage-event-date">{ev.date}</span>}
                <span className="caiso-stage-event-label">{ev.label}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {isCompleted && (
        <div className="caiso-stage caiso-stage-complete">
          <span className="caiso-stage-marker complete">✓</span>
          <span className="caiso-stage-event-label">Completed</span>
        </div>
      )}
    </div>
  );
};

// Horizontal milestone strip: Initiated → (Work begins) → Target/Actual.
// Today marker overlays at its proportional position when the issue is open.
// Used by PJM; CAISO uses CaisoTimeline instead (different data shape).
const PjmTimeline = ({ issue }) => {
  const init   = issue.initiated_date;
  const work   = issue.work_begins_date;
  const target = issue.target_completion_date;
  const actual = issue.actual_completion_date;

  // We need at least a start and an end to draw a proportional bar.
  if (!init || !target) {
    const fallback = [];
    if (init)   fallback.push({ label: "Initiated",  date: init });
    if (work)   fallback.push({ label: "Work begins", date: work });
    if (target) fallback.push({ label: "Target",     date: target });
    if (actual) fallback.push({ label: "Completed",  date: actual });
    if (!fallback.length) return null;
    return (
      <div className="initiative-timeline-text">
        {fallback.map(f => (
          <span key={f.label}>
            <span className="initiative-tl-meta">{f.label}</span>{" "}
            <span className="initiative-tl-date">{formatShortDate(f.date)}</span>
          </span>
        ))}
      </div>
    );
  }

  const startMs = Date.parse(init   + "T12:00:00");
  const endMs   = Date.parse(target + "T12:00:00");
  const workMs  = work   ? Date.parse(work   + "T12:00:00") : null;
  const actMs   = actual ? Date.parse(actual + "T12:00:00") : null;
  const todayMs = Date.now();

  const range = Math.max(1, endMs - startMs);
  const clamp = (pct) => Math.max(0, Math.min(100, pct));
  const todayPct = clamp(((todayMs - startMs) / range) * 100);
  const workPct  = workMs ? clamp(((workMs  - startMs) / range) * 100) : null;

  const isComplete = !!actMs;
  const isOverdue  = !isComplete && todayMs > endMs;
  const fillPct    = isComplete ? 100 : todayPct;
  const showWorkDot = workPct != null && workPct > 4 && workPct < 96;

  const endLabel =
    isComplete ? "Completed" :
    isOverdue  ? "Past target" : "Target";
  const endDate = isComplete ? actual : target;

  return (
    <div className="initiative-timeline">
      <div className={"initiative-tl-track" + (isOverdue ? " overdue" : "") + (isComplete ? " complete" : "")}>
        <div className="initiative-tl-fill" style={{ width: `${fillPct}%` }}/>
        <span className="initiative-tl-dot start" title={`Initiated ${formatShortDate(init)}`}/>
        {showWorkDot && (
          <span className="initiative-tl-dot work" style={{ left: `${workPct}%` }}
                title={`Work begins ${formatShortDate(work)}`}/>
        )}
        <span className="initiative-tl-dot end" title={`${endLabel} ${formatShortDate(endDate)}`}/>
        {!isComplete && todayPct > 0 && todayPct < 100 && (
          <span className="initiative-tl-today" style={{ left: `${todayPct}%` }} title="Today"/>
        )}
      </div>
      <div className="initiative-tl-labels">
        <span>
          <span className="initiative-tl-date">{formatShortDate(init)}</span>
          <span className="initiative-tl-meta">Initiated</span>
        </span>
        <span style={{ textAlign: "right" }}>
          <span className="initiative-tl-date">{formatShortDate(endDate)}</span>
          <span className="initiative-tl-meta">{endLabel}</span>
        </span>
      </div>
    </div>
  );
};

const IssueTimeline = ({ issue }) => {
  if (issue.rto === "CAISO") return <CaisoTimeline issue={issue}/>;
  return <PjmTimeline issue={issue}/>;
};

const InitiativeCard = ({ issue }) => {
  const isClosed = issue.is_open === false;
  return (
    <div className={"initiative-card" + (isClosed ? " closed" : "")}>
      <div className="initiative-card-head">
        <a href={issue.url || "#"} target="_blank" rel="noopener" className="initiative-name">
          {issue.canonical_name || "(unnamed initiative)"}
          <Icon name="external" size={11}/>
        </a>
        <span className={"initiative-status" + (isClosed ? " closed" : "")}>
          {formatStatus(issue.status)}
        </span>
      </div>
      {issue.committee_owner_label && (
        <div className="initiative-meta">{issue.committee_owner_label}</div>
      )}
      <IssueTimeline issue={issue}/>
    </div>
  );
};

const DocCard = ({ d, event, onOpenDoc }) => {
  const isHydro = d.hydro_relevant;
  return (
    <div className={"doc-card" + (isHydro ? " hydro" : "")} onClick={() => onOpenDoc(d, event)}>
      <div className={"doc-icon" + (isHydro ? " hydro" : "")}>PDF</div>
      <div className="doc-body">
        <div className="doc-title">
          {d.title}
          {isHydro && (
            <span className="hydro-flag" style={{ fontSize: 9 }}>
              <span className="hydro-tri"/> hydro
            </span>
          )}
        </div>
        <div className="doc-meta">
          <span style={{ textTransform: "capitalize" }}>{d.type}</span>
          {d.filename && <><span>·</span><span>{d.filename}</span></>}
        </div>
        {d.ai_summary && (
          <div className="doc-summary" style={isHydro ? null : { color: "var(--text-muted)" }}>
            {d.ai_summary}
          </div>
        )}
        {isHydro && d.hydro_relevance_reason && (
          <div className="doc-why"><span className="label">Why</span>{d.hydro_relevance_reason}</div>
        )}
      </div>
      {isHydro && (
        <div className="doc-actions">
          <button className="btn" style={{ height: 26, fontSize: 11 }}>
            <Icon name="eye" size={12}/> Read
          </button>
        </div>
      )}
    </div>
  );
};

const DetailPane = ({ event, onClose, onOpenDoc }) => {
  // Hooks must run in the same order on every render — declare before the
  // null-event early return.
  const [activeTabState, setActiveTab] = React.useState(null);

  if (!event) return (
    <>
      <div className="detail-overlay"/>
      <div className="detail-panel"/>
    </>
  );
  const e = event;
  const hydroDocs = e.documents.filter(d => d.hydro_relevant);
  const otherDocs = e.documents.filter(d => !d.hydro_relevant);

  const openSource = () => {
    const url = e.detailUrl || e.sourceUrl || e.materialsUrl;
    if (url) window.open(url, "_blank", "noopener");
  };

  const tabs = [];
  if (e.issues && e.issues.length > 0) {
    tabs.push({ id: "initiatives", label: "Initiatives", icon: "target", count: e.issues.length });
  }
  if (e.documents.length > 0) {
    tabs.push({ id: "docs", label: "Documents", icon: "folder", count: e.documents.length });
  }
  // If the previously-selected tab is invalid for this event (e.g. user
  // opened an event without initiatives after viewing one with them), fall
  // back to the first available rather than render an empty panel.
  const validIds = tabs.map(t => t.id);
  const activeTab = validIds.includes(activeTabState) ? activeTabState : (tabs[0] && tabs[0].id);

  return (
    <>
      <div className="detail-overlay open" onClick={onClose}/>
      <div className="detail-panel open">
        <div className="detail-head">
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16}/></button>
          <div className="detail-head-title">Meeting detail</div>
          <button className="icon-btn" title="Open source" onClick={openSource}><Icon name="external" size={14}/></button>
        </div>
        <div className="detail-body">
          <div className="detail-hero">
            <div className="detail-hero-tags">
              <RtoTag rto={e.rto} meta={e.rtoMeta}/>
              {e.committee && (
                <span className="rto-tag" style={{ background: "var(--bg-sunken)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                  {e.committee}
                </span>
              )}
              {e.isRelevant && (
                <span className="hydro-flag">
                  <span className="hydro-tri"/> Hydro-relevant
                </span>
              )}
              {e.hasIssues && (
                <span className="initiative-flag">
                  <Icon name="target" size={10}/> Initiative-linked
                </span>
              )}
            </div>
            <h1>{e.title}</h1>
            <div className="detail-hero-meta">
              <div className="detail-hero-meta-item">
                <Icon name="calendar" size={14}/>
                <div>
                  <div className="label">Date</div>
                  <div>{new Date(e.date+"T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}</div>
                </div>
              </div>
              <div className="detail-hero-meta-item">
                <Icon name="clock" size={14}/>
                <div>
                  <div className="label">Time</div>
                  <div>{e.timeFmt || "TBD"}</div>
                </div>
              </div>
              <div className="detail-hero-meta-item">
                <Icon name="folder" size={14}/>
                <div>
                  <div className="label">Documents</div>
                  <div>{e.documents.length} attached{hydroDocs.length > 0 ? ` · ${hydroDocs.length} hydro-relevant` : ""}</div>
                </div>
              </div>
            </div>

            {e.meetingHydroRelevant && e.meetingHydroReason && (
              <div className="hydro-banner">
                <div className="hydro-banner-icon">▲</div>
                <div className="hydro-banner-body">
                  <div className="hydro-banner-label">Why this matters for hydro</div>
                  <div>{e.meetingHydroReason}</div>
                </div>
              </div>
            )}

            <div className="detail-hero-actions">
              <button className="btn primary" onClick={openSource} disabled={!e.detailUrl && !e.sourceUrl}>
                <Icon name="external" size={14}/> Open on {e.rtoMeta.label}
              </button>
            </div>
          </div>

          {tabs.length > 0 && (
            <div className="detail-tabs">
              {tabs.map(t => (
                <div
                  key={t.id}
                  className={"tab" + (activeTab === t.id ? " active" : "")}
                  onClick={() => setActiveTab(t.id)}>
                  <Icon name={t.icon} size={12}/>
                  <span>{t.label}</span>
                  <span className="tab-count">{t.count}</span>
                </div>
              ))}
            </div>
          )}

          {activeTab === "initiatives" && (
            <div className="tab-panel">
              {e.issues.map(issue => (
                <InitiativeCard key={`${issue.rto}:${issue.native_id}`} issue={issue}/>
              ))}
            </div>
          )}

          {activeTab === "docs" && (
            <div className="tab-panel">
              {hydroDocs.length > 0 && (
                <>
                  <div className="tab-subhead"><span className="hydro-tri"/> Hydro-relevant · {hydroDocs.length}</div>
                  {hydroDocs.map(d => <DocCard key={d.id} d={d} event={e} onOpenDoc={onOpenDoc}/>)}
                </>
              )}
              {otherDocs.length > 0 && (
                <>
                  {hydroDocs.length > 0 && <div className="tab-subhead">Other documents · {otherDocs.length}</div>}
                  {otherDocs.map(d => <DocCard key={d.id} d={d} event={e} onOpenDoc={onOpenDoc}/>)}
                </>
              )}
            </div>
          )}

          {tabs.length === 0 && (
            <div className="section" style={{ color: "var(--text-muted)", fontSize: 13 }}>
              No documents attached yet. The scraper will pick them up at the next scheduled run.
            </div>
          )}
        </div>
      </div>
    </>
  );
};

window.DetailPane = DetailPane;

// ─── Doc Reader ────────────────────────────────────────────────
// Single-column AI summary view. Source PDF opens in a new tab —
// we don't try to iframe it (most RTOs set X-Frame-Options: DENY).

const DocReader = ({ doc, event, onClose }) => {
  if (!doc) return null;

  const sourceUrl = doc.url || event.sourceUrl;
  const openSource = () => {
    if (sourceUrl) window.open(sourceUrl, "_blank", "noopener");
  };

  let sourceHost = null;
  try { if (sourceUrl) sourceHost = new URL(sourceUrl).hostname; } catch (_) {}

  return (
    <>
      <div className="reader-overlay open" onClick={onClose}/>
      <div className="reader">
        <div className="reader-head">
          <button className="reader-back" onClick={onClose}>
            <Icon name="chevronLeft" size={14}/> Back to meeting
          </button>
          <span style={{ color: "var(--text-soft)" }}>/</span>
          <div className="reader-doc-title">{doc.title}</div>
          {doc.hydro_relevant && (
            <span className="hydro-flag"><span className="hydro-tri"/> hydro</span>
          )}
          <button className="btn primary" style={{ height: 28 }} onClick={openSource} disabled={!sourceUrl}>
            <Icon name="external" size={12}/> Open source PDF
          </button>
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16}/></button>
        </div>
        <div style={{ overflowY: "auto", background: "var(--bg)" }}>
          <div className="reader-summary" style={{
            maxWidth: 760,
            margin: "0 auto",
            border: "none",
            background: "transparent",
            padding: "var(--sp-6) var(--sp-5)",
          }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600 }}>
              <Icon name="sparkle" size={11}/> AI summary · screened by Claude
            </div>
            <h2>{doc.title}</h2>
            <div style={{ display: "flex", gap: 12, fontSize: 12, color: "var(--text-muted)", marginBottom: 20, flexWrap: "wrap" }}>
              <span><strong style={{ color: "var(--text)" }}>{event.rtoMeta.label}</strong></span>
              <span>·</span>
              <span style={{ textTransform: "capitalize" }}>{doc.type}</span>
              {doc.posted_date && <><span>·</span><span>posted {doc.posted_date}</span></>}
              {doc.filename && <><span>·</span><span style={{ fontFamily: "var(--font-mono, monospace)" }}>{doc.filename}</span></>}
            </div>

            {doc.hydro_relevant && doc.hydro_relevance_reason && (
              <div className="callout" style={{ marginBottom: 20 }}>
                <div className="callout-icon">▲</div>
                <div className="callout-body">
                  <div className="label">Why this matters</div>
                  {doc.hydro_relevance_reason}
                </div>
              </div>
            )}

            {doc.ai_summary ? (
              <div className="reader-summary-section">
                <h4>Summary</h4>
                <p>{doc.ai_summary}</p>
              </div>
            ) : (
              <div className="reader-summary-section" style={{ color: "var(--text-muted)" }}>
                <h4>Summary</h4>
                <p>This document hasn't been screened yet. The next scheduled scraper run will extract its text and generate a summary.</p>
              </div>
            )}

            {sourceUrl && (
              <div style={{
                marginTop: "var(--sp-6)",
                padding: "var(--sp-4)",
                background: "var(--bg-elev)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, marginBottom: 4 }}>
                    Source
                  </div>
                  <div style={{ fontSize: 12, fontFamily: "var(--font-mono, monospace)", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {sourceUrl}
                  </div>
                </div>
                <button className="btn primary" onClick={openSource}>
                  <Icon name="external" size={14}/> Open on {sourceHost || event.rtoMeta.label}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
};

window.DocReader = DocReader;
