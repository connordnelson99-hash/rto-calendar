// detail-pane.jsx — meeting detail drawer + doc reader.
// All synthetic content removed. AI summary / hydro flag come from
// the screening pipeline (rto_events_with_docs.json) and render only
// when present.

const DetailPane = ({ event, onClose, onOpenDoc }) => {
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

          {e.meetingHydroRelevant && e.meetingHydroReason && (
            <div className="section">
              <div className="callout">
                <div className="callout-icon">▲</div>
                <div className="callout-body">
                  <div className="label">Why this matters for hydro</div>
                  {e.meetingHydroReason}
                </div>
              </div>
            </div>
          )}

          {hydroDocs.length > 0 && (
            <div className="section">
              <h3><span className="hydro-tri"/> Hydro-relevant documents · {hydroDocs.length}</h3>
              {hydroDocs.map(d => (
                <div key={d.id} className="doc-card hydro" onClick={() => onOpenDoc(d, e)}>
                  <div className="doc-icon hydro">PDF</div>
                  <div className="doc-body">
                    <div className="doc-title">
                      {d.title}
                      <span className="hydro-flag" style={{ fontSize: 9 }}>
                        <span className="hydro-tri"/> hydro
                      </span>
                    </div>
                    <div className="doc-meta">
                      <span style={{ textTransform: "capitalize" }}>{d.type}</span>
                      {d.filename && <><span>·</span><span>{d.filename}</span></>}
                    </div>
                    {d.ai_summary && <div className="doc-summary">{d.ai_summary}</div>}
                    {d.hydro_relevance_reason && (
                      <div className="doc-why"><span className="label">Why</span>{d.hydro_relevance_reason}</div>
                    )}
                  </div>
                  <div className="doc-actions">
                    <button className="btn" style={{ height: 26, fontSize: 11 }}>
                      <Icon name="eye" size={12}/> Read
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {otherDocs.length > 0 && (
            <div className="section">
              <h3>Other documents · {otherDocs.length}</h3>
              {otherDocs.map(d => (
                <div key={d.id} className="doc-card" onClick={() => onOpenDoc(d, e)}>
                  <div className="doc-icon">PDF</div>
                  <div className="doc-body">
                    <div className="doc-title">{d.title}</div>
                    <div className="doc-meta">
                      <span style={{ textTransform: "capitalize" }}>{d.type}</span>
                      {d.filename && <><span>·</span><span>{d.filename}</span></>}
                    </div>
                    {d.ai_summary && (
                      <div className="doc-summary" style={{ color: "var(--text-muted)" }}>{d.ai_summary}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {e.documents.length === 0 && (
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
