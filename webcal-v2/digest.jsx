// digest.jsx — morning digest modal. All counts and copy derive from the live feed.

const DigestModal = ({ open, onClose, onOpenEvent }) => {
  if (!open) return null;
  const data = window.MARKETS_DATA;
  const items = data.digestItems;
  const today = data.today;
  const hydroItems = items.filter(e => e.isRelevant);
  const totalDocs = items.reduce((sum, e) => sum + e.documents.length, 0);
  const totalHydroDocs = items.reduce((sum, e) => sum + e.hydroDocCount, 0);
  const rtosCovered = Array.from(new Set(items.map(e => e.rtoMeta.label)));

  const todayD = new Date(today + "T12:00:00");
  const headerDate = todayD.toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric", year: "numeric"
  });

  return (
    <>
      <div className="reader-overlay open" onClick={onClose}/>
      <div className="reader" style={{ inset: "8vh 10vw", display: "grid", gridTemplateRows: "48px 1fr" }}>
        <div className="reader-head">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 28, height: 28, borderRadius: 6, background: "var(--hydro)", color: "white", display: "grid", placeItems: "center" }}>
              <Icon name="sparkle" size={14}/>
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Morning digest</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{headerDate}</div>
            </div>
          </div>
          <div style={{ flex: 1 }}/>
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16}/></button>
        </div>
        <div style={{ overflowY: "auto", padding: "32px 48px", background: "var(--bg)" }}>
          <div style={{ maxWidth: 720, margin: "0 auto" }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 8 }}>
              Daily brief · National Hydropower Association
            </div>
            <h1 style={{ fontSize: 32, fontWeight: 600, margin: "0 0 8px", letterSpacing: "-0.02em", lineHeight: 1.15 }}>
              {hydroItems.length === 0
                ? "No hydro-relevant meetings flagged this week"
                : `${hydroItems.length} hydro-relevant meeting${hydroItems.length === 1 ? "" : "s"} this week`}
            </h1>
            {rtosCovered.length > 0 && (
              <div style={{ fontSize: 15, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 24 }}>
                Coverage across {rtosCovered.join(", ")}.
                {totalHydroDocs > 0 && ` ${totalHydroDocs} hydro-relevant document${totalHydroDocs === 1 ? "" : "s"} attached.`}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 32 }}>
              <div style={{ padding: 14, background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 4 }}>This week</div>
                <div style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{hydroItems.length}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>hydro-relevant meetings</div>
              </div>
              <div style={{ padding: 14, background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 4 }}>Hydro-flagged docs</div>
                <div style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{totalHydroDocs}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>screened by Claude</div>
              </div>
              <div style={{ padding: 14, background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 4 }}>Total documents</div>
                <div style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{totalDocs}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>attached to digest items</div>
              </div>
            </div>

            {hydroItems.length > 0 && (
              <>
                <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 12 }}>
                  Top items to read
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {hydroItems.slice(0, 5).map(e => (
                    <div key={e.id} onClick={() => { onOpenEvent(e.id); onClose(); }}
                      style={{ display: "grid", gridTemplateColumns: "60px 1fr auto", gap: 14, padding: 14,
                               background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8, cursor: "pointer" }}>
                      <div style={{ textAlign: "center" }}>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase" }}>
                          {new Date(e.date+"T12:00:00").toLocaleDateString("en-US", { month: "short" })}
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 600, lineHeight: 1.1 }}>{new Date(e.date+"T12:00:00").getDate()}</div>
                        <div style={{ fontSize: 10, color: "var(--text-soft)" }}>{new Date(e.date+"T12:00:00").toLocaleDateString("en-US", { weekday: "short" })}</div>
                      </div>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                          <RtoTag rto={e.rto} meta={e.rtoMeta}/>
                          <span style={{ fontSize: 13, fontWeight: 500 }}>{e.title}</span>
                        </div>
                        {e.meetingHydroReason && (
                          <div style={{ fontSize: 12, color: "var(--hydro-strong)", lineHeight: 1.5 }}>
                            ▲ {e.meetingHydroReason}
                          </div>
                        )}
                        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                          {e.documents.length} doc{e.documents.length === 1 ? "" : "s"}
                          {e.hydroDocCount > 0 && ` · ${e.hydroDocCount} hydro-relevant`}
                          {e.timeFmt && ` · ${e.timeFmt}`}
                        </div>
                      </div>
                      <Icon name="arrowRight" size={16}/>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
};

window.DigestModal = DigestModal;
