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

// Doc-type display + ranking. Lower weight = more newsworthy → sorts first.
// Keys match the screener's `type` values; "other"/"document" carry no signal.
const DOC_TYPE_ORDER = [
  "report", "decision", "proposal", "vote", "issue-charge", "tariff",
  "presentation", "matrix", "agenda", "comment", "minutes", "manual",
  "press-release", "elibrary", "other", "document",
];
function docTypeWeight(t) {
  const i = DOC_TYPE_ORDER.indexOf(t);
  return i === -1 ? DOC_TYPE_ORDER.length : i;
}
function docTypeLabel(t) {
  const s = String(t || "document").replace(/_/g, " ");
  return ["other", "document"].includes(s) ? "Other" : s;
}

function daysBetweenIso(a, b) {
  if (!a || !b) return Infinity;
  return Math.abs(Date.parse(a + "T12:00:00") - Date.parse(b + "T12:00:00")) / 86400000;
}

// Cross-RTO comparison (free, no API): given a document, find summaries of
// similar materials — same doc type and/or overlapping topic tags — from OTHER
// RTOs within a ~10-day window, so members can eyeball how peers are treating
// the same issue without leaving the calendar. Pure scan of the loaded feed.
function findRelatedDocs(doc, event, { windowDays = 10, limit = 12 } = {}) {
  const data = window.MARKETS_DATA;
  if (!data || !doc) return [];
  const topics = new Set(doc.topics || []);
  const out = [];
  for (const ev of (data.events || [])) {
    if (ev.id === event.id || ev.rto === event.rto) continue;   // other RTOs only
    if (daysBetweenIso(ev.date, event.date) > windowDays) continue;
    for (const od of (ev.documents || [])) {
      if (!od.ai_summary) continue;
      const shared = (od.topics || []).filter(t => topics.has(t));
      const sameType = od.type === doc.type;
      if (!shared.length && !sameType) continue;                // share theme or type
      out.push({
        ev, doc: od, shared, sameType,
        score: (sameType ? 4 : 0) + shared.length,
        proximity: daysBetweenIso(ev.date, event.date),
      });
    }
  }
  // Prefer strong matches (shared theme AND same type); fall back to the
  // broader set when strong matches are too few to be worth a column.
  const strong = out.filter(r => r.sameType && r.shared.length);
  const pool = strong.length >= 2 ? strong : out;
  pool.sort((a, b) => b.score - a.score || a.proximity - b.proximity);
  return pool.slice(0, limit);
}

// Group related docs into per-RTO columns (most-relevant RTO first).
function groupRelatedByRto(related) {
  const byRto = new Map();
  for (const r of related) {
    if (!byRto.has(r.ev.rto)) {
      byRto.set(r.ev.rto, { rto: r.ev.rto, meta: r.ev.rtoMeta, items: [], best: 0 });
    }
    const g = byRto.get(r.ev.rto);
    g.items.push(r);
    g.best = Math.max(g.best, r.score);
  }
  return [...byRto.values()].sort((a, b) => b.best - a.best || b.items.length - a.items.length);
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

// "April 16, 2026" / "Sep 26, 2023*" → ISO "yyyy-mm-dd". Asterisks mark
// tentative dates in CAISO's text — we treat tentative the same as known
// for visual placement.
function caisoDateToIso(raw) {
  if (!raw) return null;
  const cleaned = String(raw).replace(/\*/g, "").trim();
  const ms = Date.parse(cleaned);
  if (isNaN(ms)) return null;
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Walk all four CAISO stages and return one entry per distinct date
// (sorted), folding duplicate dates' labels together with " · ".
function collectCaisoEvents(issue) {
  const byDate = new Map();
  for (const key of ["stage_a", "stage_b", "stage_c", "stage_d"]) {
    for (const ev of parseCaisoStage(issue[key])) {
      const iso = caisoDateToIso(ev.date);
      if (!iso) continue;
      const labels = byDate.get(iso) || [];
      if (ev.label) labels.push(ev.label);
      byDate.set(iso, labels);
    }
  }
  return Array.from(byDate.entries())
    .map(([date, labels]) => ({ date, label: labels.join(" · ") }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

// Horizontal milestone strip used by both PJM and CAISO. Endpoints carry
// descriptive labels (`startLabel`/`endLabel`); intermediate `midpoints`
// (array of `{date, label}`) render as inner dots. The today marker only
// renders when the issue is open and today falls within range.
// `allowOverdue=false` (CAISO) keeps the bar neutral when today is past
// `endDate`, since for CAISO the end is just the latest known event,
// not a target the issue can blow past. `labelStyle="descriptive"` uses
// readable case + ellipsis truncation suited to long milestone names;
// the default "tag" matches PJM's terse uppercase chips.
const MilestoneBar = ({
  startDate, endDate,
  startLabel, endLabel,
  overdueLabel,
  completeLabel = "Completed",
  midpoints = [],
  isComplete = false,
  allowOverdue = true,
  labelStyle = "tag",
}) => {
  if (!startDate || !endDate) {
    const fallback = [];
    if (startDate) fallback.push({ label: startLabel, date: startDate });
    for (const mp of midpoints) fallback.push({ label: mp.label, date: mp.date });
    if (endDate) fallback.push({ label: endLabel, date: endDate });
    if (!fallback.length) return null;
    return (
      <div className="initiative-timeline-text">
        {fallback.map((f, i) => (
          <span key={i}>
            <span className="initiative-tl-meta">{f.label}</span>{" "}
            <span className="initiative-tl-date">{formatShortDate(f.date)}</span>
          </span>
        ))}
      </div>
    );
  }

  const startMs = Date.parse(startDate + "T12:00:00");
  const endMs   = Date.parse(endDate   + "T12:00:00");
  const todayMs = Date.now();

  const range = Math.max(1, endMs - startMs);
  const clamp = (pct) => Math.max(0, Math.min(100, pct));
  const todayPct = clamp(((todayMs - startMs) / range) * 100);

  const isOverdue = allowOverdue && !isComplete && todayMs > endMs;
  const fillPct   = isComplete ? 100 : todayPct;
  const resolvedEndLabel = isComplete ? completeLabel : (isOverdue ? (overdueLabel || endLabel) : endLabel);

  const labelsClass = "initiative-tl-labels" + (labelStyle === "descriptive" ? " descriptive" : "");

  return (
    <div className="initiative-timeline">
      <div className={"initiative-tl-track" + (isOverdue ? " overdue" : "") + (isComplete ? " complete" : "")}>
        <div className="initiative-tl-fill" style={{ width: `${fillPct}%` }}/>
        <span className="initiative-tl-dot start" title={`${startLabel} · ${formatShortDate(startDate)}`}/>
        {midpoints.map((mp, i) => {
          const ms = Date.parse(mp.date + "T12:00:00");
          if (isNaN(ms)) return null;
          const pct = clamp(((ms - startMs) / range) * 100);
          if (pct <= 3 || pct >= 97) return null;
          return (
            <span key={i} className="initiative-tl-dot mid"
                  style={{ left: `${pct}%` }}
                  title={`${mp.label} · ${formatShortDate(mp.date)}`}/>
          );
        })}
        <span className="initiative-tl-dot end" title={`${resolvedEndLabel} · ${formatShortDate(endDate)}`}/>
        {!isComplete && todayPct > 0 && todayPct < 100 && (
          <span className="initiative-tl-today" style={{ left: `${todayPct}%` }} title="Today"/>
        )}
      </div>
      <div className={labelsClass}>
        <span title={startLabel}>
          <span className="initiative-tl-date">{formatShortDate(startDate)}</span>
          <span className="initiative-tl-meta">{startLabel}</span>
        </span>
        <span style={{ textAlign: "right" }} title={resolvedEndLabel}>
          <span className="initiative-tl-date">{formatShortDate(endDate)}</span>
          <span className="initiative-tl-meta">{resolvedEndLabel}</span>
        </span>
      </div>
    </div>
  );
};

// Fallback for CAISO issues with no parseable dates anywhere — the
// vertical Stage A–D list rendering we used before MilestoneBar existed.
const CaisoStageList = ({ issue }) => {
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

const PjmTimeline = ({ issue }) => {
  const isComplete = !!issue.actual_completion_date;
  const midpoints = issue.work_begins_date
    ? [{ date: issue.work_begins_date, label: "Work begins" }]
    : [];
  return <MilestoneBar
    startDate={issue.initiated_date}
    endDate={isComplete ? issue.actual_completion_date : issue.target_completion_date}
    startLabel="Initiated"
    endLabel="Target"
    overdueLabel="Past target"
    completeLabel="Completed"
    midpoints={midpoints}
    isComplete={isComplete}
    allowOverdue={true}
    labelStyle="tag"
  />;
};

const CaisoTimeline = ({ issue }) => {
  const events = collectCaisoEvents(issue);
  const isComplete = issue.stage_d === "Completed" || issue.status === "Completed";
  // Need at least two distinct dates to draw a meaningful bar; otherwise
  // fall back to the vertical stage list so the user still sees something.
  if (events.length < 2) return <CaisoStageList issue={issue}/>;
  const first = events[0];
  const last = events[events.length - 1];
  const midpoints = events.slice(1, -1);
  return <MilestoneBar
    startDate={first.date}
    endDate={last.date}
    startLabel={first.label || "First milestone"}
    endLabel={last.label || "Latest milestone"}
    completeLabel="Completed"
    midpoints={midpoints}
    isComplete={isComplete}
    allowOverdue={false}
    labelStyle="descriptive"
  />;
};

// ERCOT shares CAISO's rendering path: its Action-history milestones are
// serialized into stage_a in the same "Month D, YYYY label" line format
// (see ercot_issues_scraper.py), with stage_d="Completed" on approval.
const IssueTimeline = ({ issue }) => {
  if (issue.rto === "CAISO" || issue.rto === "ERCOT") return <CaisoTimeline issue={issue}/>;
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

// Different cover pages reference the same company under slightly different
// names (e.g. "Constellation" vs "Constellation Energy Generation, LLC";
// "PJM" vs "PJM Interconnection"). Normalize to one canonical display name
// so they collapse into a single group.
const ENTITY_ALIASES = {
  "pjm interconnection":               "PJM",
  "pjm interconnection l.l.c":         "PJM",
  "pjm interconnection llc":           "PJM",
  "constellation energy":              "Constellation",
  "constellation energy generation":   "Constellation",
  "constellation power":               "Constellation",
  "exelon generation":                 "Exelon",
  "duke energy ohio":                  "Duke Energy",
  "duke energy ohio & kentucky":       "Duke Energy",
  "duke energy carolinas":             "Duke Energy",
  "first energy":                      "FirstEnergy",
  "ferc":                              "FERC",
};
const ENTITY_SUFFIX_RE =
  /[,.]?\s+(?:l\.?l\.?c\.?|llc|inc\.?|incorporated|corp\.?|corporation|co\.?|company|ltd\.?|limited|l\.?p\.?|llp|holdings?|group)\s*$/i;
function normalizeEntity(name) {
  if (!name) return null;
  // Strip trailing parenthetical abbreviation: "Foo Bar (FB)" -> "Foo Bar"
  let n = String(name).trim().replace(/\s*\([^)]+\)\s*$/g, "").trim();
  // Strip a single trailing corporate suffix
  n = n.replace(ENTITY_SUFFIX_RE, "").trim();
  // Apply alias map (lowercased lookup)
  const aliased = ENTITY_ALIASES[n.toLowerCase()];
  return aliased || n || null;
}

// Group a document's stakeholders by entity for compact display.
// Returns [{entity, people: [{name, role, email}, ...]}]
function groupStakeholdersByEntity(stakeholders) {
  const byEntity = new Map();
  for (const s of (stakeholders || [])) {
    const key = normalizeEntity(s.entity) || "Unaffiliated";
    if (!byEntity.has(key)) byEntity.set(key, []);
    byEntity.get(key).push(s);
  }
  return [...byEntity.entries()]
    .map(([entity, people]) => ({ entity, people }))
    .sort((a, b) => a.entity.localeCompare(b.entity));
}

const DocCard = ({ d, event, onOpenSummary }) => {
  const isHydro = d.hydro_relevant;
  const typeText = (d.type || "document").replace(/_/g, " ");
  // Every RTO gets the type badge; "other"/"document" carry no signal,
  // so those render without one rather than shouting OTHER.
  const showTypeTag = !["other", "document"].includes(typeText);
  const stakeholderEntities = [
    ...new Set(
      (d.stakeholders || [])
        .map(s => normalizeEntity(s.entity))
        .filter(Boolean)
    )
  ];
  // The card body opens the full-summary popup; the PDF button (below) stops
  // propagation so it still goes straight to the source.
  const openSummary = () => { if (d.ai_summary) onOpenSummary(d); };
  const openPdf = (ev) => {
    ev.stopPropagation();
    const url = d.url || event.sourceUrl;
    if (url) window.open(url, "_blank", "noopener");
  };
  const clickable = !!d.ai_summary;
  return (
    <div className={"doc-card" + (isHydro ? " hydro" : "") + (clickable ? " clickable" : "")}
         onClick={clickable ? openSummary : undefined}
         title={clickable ? "Open full summary" : null}>
      <div className="doc-body">
        {showTypeTag && (
          <div className="doc-type-tag">{typeText}</div>
        )}
        <div className="doc-title">
          {d.title}
        </div>
        {d.filename && (
          <div className="doc-meta">
            <span>{d.filename}</span>
          </div>
        )}
        {(d.topics || []).length > 0 && (
          <div className="doc-sponsors">
            <Icon name="tag" size={11}/>
            {d.topics.map(t => window.TOPIC_META?.[t]?.label || t).join(" · ")}
          </div>
        )}
        {stakeholderEntities.length > 0 && (
          <div className="doc-sponsors">
            <Icon name="users" size={11}/>
            {stakeholderEntities.slice(0, 3).join(" · ")}
            {stakeholderEntities.length > 3 && ` +${stakeholderEntities.length - 3} more`}
          </div>
        )}
        {d.ai_summary && (
          <div className="doc-summary clamp" style={isHydro ? null : { color: "var(--text-muted)" }}>
            {d.ai_summary}
          </div>
        )}
        {clickable && (
          <div className="doc-readmore">
            <Icon name="sparkle" size={11}/> Full summary &amp; cross-RTO compare
          </div>
        )}
      </div>
      <div className="doc-actions">
        <button className="btn" style={{ height: 26, fontSize: 11 }} onClick={openPdf}>
          <Icon name="external" size={12}/> Open PDF
        </button>
      </div>
    </div>
  );
};

// Full-summary popup with a cross-RTO comparison rail. Opened by clicking a
// doc card. The comparison columns are built live from the loaded feed
// (findRelatedDocs) — no API calls, no export round-trip.
const RelatedDocCard = ({ r, anchorDate }) => {
  const od = r.doc;
  const openPdf = () => { if (od.url) window.open(od.url, "_blank", "noopener"); };
  const dayGap = Math.round(daysBetweenIso(r.ev.date, anchorDate));
  return (
    <div className="cmp-card" onClick={openPdf} title="Open source PDF">
      <div className="cmp-card-top">
        {docTypeLabel(od.type) !== "Other" && (
          <span className="cmp-card-type">{docTypeLabel(od.type)}</span>
        )}
        <span className="cmp-card-date">
          {formatShortDate(r.ev.date)}{dayGap > 0 ? ` · ${dayGap}d` : ""}
        </span>
      </div>
      <div className="cmp-card-title">{od.title}</div>
      <div className="cmp-card-summary clamp">{od.ai_summary}</div>
      {r.shared.length > 0 && (
        <div className="cmp-card-topics">
          {r.shared.map(t => (
            <span key={t} className="cmp-topic-chip">{window.TOPIC_META?.[t]?.label || t}</span>
          ))}
        </div>
      )}
    </div>
  );
};

const DocSummaryModal = ({ doc, event, onClose }) => {
  if (!doc) return null;
  const related = findRelatedDocs(doc, event);
  const columns = groupRelatedByRto(related);
  const openPdf = () => {
    const url = doc.url || event.sourceUrl;
    if (url) window.open(url, "_blank", "noopener");
  };
  return (
    <>
      <div className="doc-modal-overlay open" onClick={onClose}/>
      <div className="doc-modal" role="dialog" aria-modal="true">
        <div className="doc-modal-head">
          <div className="doc-modal-head-tags">
            <RtoTag rto={event.rto} meta={event.rtoMeta}/>
            {docTypeLabel(doc.type) !== "Other" && (
              <span className="doc-type-tag" style={{ margin: 0 }}>{docTypeLabel(doc.type)}</span>
            )}
            {doc.hydro_relevant && (
              <span className="hydro-flag"><span className="hydro-tri"/> hydro</span>
            )}
          </div>
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16}/></button>
        </div>

        <div className="doc-modal-body">
          <div className="doc-modal-main">
            <h2 className="doc-modal-title">{doc.title}</h2>
            <div className="doc-modal-meta">
              <span><strong>{event.rtoMeta.label}</strong></span>
              {doc.posted_date && <><span>·</span><span>posted {doc.posted_date}</span></>}
              {doc.filename && <><span>·</span><span className="mono">{doc.filename}</span></>}
            </div>

            {(doc.topics || []).length > 0 && (
              <div className="doc-modal-topics">
                {doc.topics.map(t => (
                  <span key={t} className="cmp-topic-chip">{window.TOPIC_META?.[t]?.label || t}</span>
                ))}
              </div>
            )}

            <div className="doc-modal-summary-label">
              <Icon name="sparkle" size={11}/> AI summary · screened by Claude
            </div>
            <p className="doc-modal-summary">{doc.ai_summary}</p>

            <button className="btn primary" onClick={openPdf}>
              <Icon name="external" size={13}/> Open source PDF
            </button>
          </div>

          <div className="doc-modal-compare">
            <div className="cmp-head">
              <div className="cmp-head-title">Similar across RTOs</div>
              <div className="cmp-head-sub">
                Same type or topic, within ±10 days · {related.length} match{related.length === 1 ? "" : "es"}
              </div>
            </div>
            {columns.length === 0 ? (
              <div className="cmp-empty">
                No comparable material from other RTOs in this window yet.
              </div>
            ) : (
              <div className="cmp-columns">
                {columns.map(col => (
                  <div key={col.rto} className="cmp-column">
                    <div className="cmp-column-head">
                      <RtoTag rto={col.rto} meta={col.meta}/>
                    </div>
                    {col.items.map((r, i) => (
                      <RelatedDocCard key={`${r.ev.id}-${i}`} r={r} anchorDate={event.date}/>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
};

// Aggregate a meeting's stakeholders across all its docs.
// Dedup by normalized (entity, name); collapse the email if any doc has one;
// remember which doc(s) each person appeared on. Skip rows where the
// "name" is just the entity name itself (Haiku occasionally does this on
// docs that have an org seal but no individual signatory).
function aggregateMeetingStakeholders(documents) {
  const map = new Map();
  for (const d of (documents || [])) {
    for (const s of (d.stakeholders || [])) {
      const name = (s.name || "").trim();
      if (!name) continue;
      const normEntity = normalizeEntity(s.entity);
      // Drop name-as-entity artifacts.
      if (normEntity && name.toLowerCase() === normEntity.toLowerCase()) continue;
      // Also catch cases where the raw entity == name (un-normalized).
      if (s.entity && name.toLowerCase() === String(s.entity).toLowerCase()) continue;

      const key = `${(normEntity || "").toLowerCase()}::${name.toLowerCase()}`;
      const existing = map.get(key);
      if (existing) {
        if (s.email && !existing.email) existing.email = s.email;
        if (s.role && !existing.roles.includes(s.role)) existing.roles.push(s.role);
        if (!existing.docs.find(x => x.id === d.id)) {
          existing.docs.push({ id: d.id, title: d.title });
        }
      } else {
        map.set(key, {
          name, entity: normEntity || null,
          email: s.email || null,
          roles: s.role ? [s.role] : [],
          docs: [{ id: d.id, title: d.title }],
        });
      }
    }
  }
  // Group by entity, sort entities by person count desc.
  const byEntity = new Map();
  for (const p of map.values()) {
    const key = p.entity || "Unaffiliated";
    if (!byEntity.has(key)) byEntity.set(key, []);
    byEntity.get(key).push(p);
  }
  return [...byEntity.entries()]
    .map(([entity, people]) => ({
      entity,
      people: people.sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .sort((a, b) => b.people.length - a.people.length || a.entity.localeCompare(b.entity));
}

const DetailPane = ({ event, onClose, onOpenDoc }) => {
  // Hooks must run in the same order on every render — declare before the
  // null-event early return.
  const [activeTabState, setActiveTab] = React.useState(null);
  const [docTypeFilter, setDocTypeFilter] = React.useState(null);
  const [summaryDoc, setSummaryDoc] = React.useState(null);

  if (!event) return (
    <>
      <div className="detail-overlay"/>
      <div className="detail-panel"/>
    </>
  );
  const e = event;

  // Doc-type filter chips: tally types present on this meeting, ranked by
  // newsworthiness. A stale filter (type absent from this event) reads as "All".
  const typeCounts = new Map();
  for (const d of e.documents) {
    const t = d.type || "document";
    typeCounts.set(t, (typeCounts.get(t) || 0) + 1);
  }
  const docTypes = [...typeCounts.entries()]
    .map(([type, count]) => ({ type, count, label: docTypeLabel(type) }))
    .sort((a, b) => docTypeWeight(a.type) - docTypeWeight(b.type));
  const activeType = typeCounts.has(docTypeFilter) ? docTypeFilter : null;
  const matchesType = (d) => !activeType || (d.type || "document") === activeType;

  const hydroDocs = e.documents.filter(d => d.hydro_relevant && matchesType(d));
  const otherDocs = e.documents.filter(d => !d.hydro_relevant && matchesType(d));

  const openSource = () => {
    const url = e.detailUrl || e.sourceUrl || e.materialsUrl;
    if (url) window.open(url, "_blank", "noopener");
  };

  const stakeholderGroups = aggregateMeetingStakeholders(e.documents);
  const stakeholderCount = stakeholderGroups.reduce((n, g) => n + g.people.length, 0);

  const tabs = [];
  if (e.issues && e.issues.length > 0) {
    tabs.push({ id: "initiatives", label: "Initiatives", icon: "target", count: e.issues.length });
  }
  if (e.documents.length > 0) {
    tabs.push({ id: "docs", label: "Documents", icon: "folder", count: e.documents.length });
  }
  if (stakeholderCount > 0) {
    tabs.push({ id: "stakeholders", label: "Stakeholders", icon: "users", count: stakeholderCount });
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
              {docTypes.length > 1 && (
                <div className="doc-type-filter">
                  <button
                    className={"doc-type-chip" + (activeType === null ? " active" : "")}
                    onClick={() => setDocTypeFilter(null)}>
                    All <span className="doc-type-chip-count">{e.documents.length}</span>
                  </button>
                  {docTypes.map(t => (
                    <button
                      key={t.type}
                      className={"doc-type-chip" + (activeType === t.type ? " active" : "")}
                      onClick={() => setDocTypeFilter(activeType === t.type ? null : t.type)}>
                      {t.label} <span className="doc-type-chip-count">{t.count}</span>
                    </button>
                  ))}
                </div>
              )}
              {hydroDocs.length === 0 && otherDocs.length === 0 && (
                <div className="section" style={{ color: "var(--text-muted)", fontSize: 13 }}>
                  No {activeType ? docTypeLabel(activeType) : ""} documents match.
                </div>
              )}
              {hydroDocs.length > 0 && (
                <>
                  <div className="tab-subhead"><span className="hydro-tri"/> Hydro-relevant · {hydroDocs.length}</div>
                  {hydroDocs.map(d => <DocCard key={d.id} d={d} event={e} onOpenSummary={setSummaryDoc}/>)}
                </>
              )}
              {otherDocs.length > 0 && (
                <>
                  {hydroDocs.length > 0 && <div className="tab-subhead">Other documents · {otherDocs.length}</div>}
                  {otherDocs.map(d => <DocCard key={d.id} d={d} event={e} onOpenSummary={setSummaryDoc}/>)}
                </>
              )}
            </div>
          )}

          {activeTab === "stakeholders" && (
            <div className="tab-panel">
              <div className="stakeholders-hint">
                Authors and contacts named on this meeting's documents.
                {" "}Emails are extracted only when they appear verbatim on the doc.
              </div>
              {stakeholderGroups.map(group => (
                <div key={group.entity} className="stakeholder-group">
                  <div className="stakeholder-entity">
                    {group.entity}
                    <span className="stakeholder-entity-count">{group.people.length}</span>
                  </div>
                  {group.people.map(p => (
                    <div key={`${p.entity || ""}::${p.name}`} className="stakeholder-person">
                      <div className="stakeholder-name">{p.name}</div>
                      {p.roles.length > 0 && (
                        <div className="stakeholder-roles">
                          {p.roles.map(r => (
                            <span key={r} className="stakeholder-role">{r}</span>
                          ))}
                        </div>
                      )}
                      {p.email && (
                        <a className="stakeholder-email" href={`mailto:${p.email}`}>
                          <Icon name="external" size={10}/>{p.email}
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}

          {tabs.length === 0 && (
            <div className="section" style={{ color: "var(--text-muted)", fontSize: 13 }}>
              No documents attached yet. The scraper will pick them up at the next scheduled run.
            </div>
          )}
        </div>
      </div>
      {summaryDoc && (
        <DocSummaryModal doc={summaryDoc} event={e} onClose={() => setSummaryDoc(null)}/>
      )}
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
