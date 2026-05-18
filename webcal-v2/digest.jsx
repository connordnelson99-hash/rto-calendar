// digest.jsx — weekly digest modal. Counts and copy derive from the live
// feed for the currently-selected ISO week (Mon–Sun). Past weeks pick up
// document-level hydro flags (which arrive after meetings), while the
// current week is mostly title-driven.

const fmtWeekRange = (a, b) => {
  const da = new Date(a + "T12:00:00");
  const db = new Date(b + "T12:00:00");
  const sameMonth = da.getMonth() === db.getMonth() && da.getFullYear() === db.getFullYear();
  const opts = { month: "short", day: "numeric" };
  const startStr = da.toLocaleDateString("en-US", opts);
  const endStr = sameMonth
    ? String(db.getDate())
    : db.toLocaleDateString("en-US", opts);
  return `${startStr} – ${endStr}, ${db.getFullYear()}`;
};

const DigestModal = ({ open, onClose, onOpenEvent }) => {
  const data = window.MARKETS_DATA;
  const weeks = (data && data.weeks) || [];
  const [selectedWeekKey, setSelectedWeekKey] = React.useState(data && data.currentWeekKey);
  const [copyState, setCopyState] = React.useState("idle");

  // Reset to the current ISO week each time the modal opens, so reopening
  // never strands the user in a stale historical week.
  React.useEffect(() => {
    if (open && data) setSelectedWeekKey(data.currentWeekKey);
  }, [open]);

  if (!open) return null;

  const selectedIdx = weeks.findIndex(w => w.key === selectedWeekKey);
  const week = selectedIdx >= 0 ? weeks[selectedIdx] : null;
  const items = week ? week.items : [];
  const isCurrentWeek = selectedWeekKey === data.currentWeekKey;
  const isFutureWeek = week && week.weekStart > data.currentWeekKey;

  const totalDocs = items.reduce((sum, e) => sum + e.documents.length, 0);
  const totalHydroDocs = items.reduce((sum, e) => sum + e.hydroDocCount, 0);
  const rtosCovered = Array.from(new Set(items.map(e => e.rtoMeta.label)));

  const canGoPrev = selectedIdx > 0;
  const canGoNext = selectedIdx >= 0 && selectedIdx < weeks.length - 1;
  const onPrev = () => { if (canGoPrev) setSelectedWeekKey(weeks[selectedIdx - 1].key); };
  const onNext = () => { if (canGoNext) setSelectedWeekKey(weeks[selectedIdx + 1].key); };

  const weekRangeLabel = week ? fmtWeekRange(week.weekStart, week.weekEnd) : "";
  const weekRelLabel = isCurrentWeek
    ? "This week"
    : isFutureWeek
      ? "Upcoming week"
      : "Past week";

  const headlineCountSuffix = isCurrentWeek
    ? " this week"
    : isFutureWeek
      ? ""
      : ` · week of ${weekRangeLabel}`;

  const emptyHeadline = isCurrentWeek
    ? "No hydro-relevant meetings flagged this week"
    : isFutureWeek
      ? "No hydro-relevant meetings flagged for this upcoming week"
      : `No hydro-relevant meetings flagged for week of ${weekRangeLabel}`;

  const exportDisabled = items.length === 0;
  const onCopy = async () => {
    if (exportDisabled) return;
    const md = window.buildDigestMarkdown(data, selectedWeekKey);
    try {
      await navigator.clipboard.writeText(md);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1800);
    } catch (err) {
      console.error("Clipboard copy failed:", err);
      setCopyState("error");
      setTimeout(() => setCopyState("idle"), 1800);
    }
  };
  const onDownload = () => {
    if (exportDisabled) return;
    const md = window.buildDigestMarkdown(data, selectedWeekKey);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `rto-digest-${week.weekStart}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  const exportBtnStyle = {
    display: "flex", alignItems: "center", gap: 6,
    padding: "6px 10px",
    background: "transparent",
    color: exportDisabled ? "var(--text-soft)" : "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    cursor: exportDisabled ? "not-allowed" : "pointer",
    fontSize: 12, fontWeight: 500,
    opacity: exportDisabled ? 0.5 : 1,
  };

  const stepBtnStyle = (disabled) => ({
    width: 26, height: 26, display: "grid", placeItems: "center",
    background: "transparent",
    color: disabled ? "var(--text-soft)" : "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
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
              <div style={{ fontSize: 14, fontWeight: 600 }}>Weekly digest</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {weekRelLabel} · {weekRangeLabel}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: 16 }}>
            <button onClick={onPrev} disabled={!canGoPrev} style={stepBtnStyle(!canGoPrev)}
              title={canGoPrev ? "Previous week" : "No earlier week in dataset"}>
              <Icon name="chevronLeft" size={14}/>
            </button>
            <button onClick={onNext} disabled={!canGoNext} style={stepBtnStyle(!canGoNext)}
              title={canGoNext ? "Next week" : "No later week in dataset"}>
              <Icon name="chevronRight" size={14}/>
            </button>
            {!isCurrentWeek && (
              <button onClick={() => setSelectedWeekKey(data.currentWeekKey)}
                style={{ ...exportBtnStyle, marginLeft: 4 }}
                title="Jump to current week">
                Today
              </button>
            )}
          </div>
          <div style={{ flex: 1 }}/>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginRight: 8 }}>
            <button onClick={onCopy} disabled={exportDisabled} style={exportBtnStyle}
              title={exportDisabled ? "Nothing to export for this week" : "Copy digest as markdown to clipboard"}>
              <Icon name={copyState === "copied" ? "check" : "copy"} size={13}/>
              {copyState === "copied" ? "Copied" : copyState === "error" ? "Copy failed" : "Copy markdown"}
            </button>
            <button onClick={onDownload} disabled={exportDisabled} style={exportBtnStyle}
              title={exportDisabled ? "Nothing to export for this week" : "Download digest as .md file"}>
              <Icon name="download" size={13}/>
              Download
            </button>
          </div>
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16}/></button>
        </div>
        <div style={{ overflowY: "auto", padding: "32px 48px", background: "var(--bg)" }}>
          <div style={{ maxWidth: 720, margin: "0 auto" }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 8 }}>
              Weekly briefing · National Hydropower Association
            </div>
            <h1 style={{ fontSize: 32, fontWeight: 600, margin: "0 0 8px", letterSpacing: "-0.02em", lineHeight: 1.15 }}>
              {items.length === 0
                ? emptyHeadline
                : `${items.length} hydro-relevant meeting${items.length === 1 ? "" : "s"}${headlineCountSuffix}`}
            </h1>
            {rtosCovered.length > 0 && (
              <div style={{ fontSize: 15, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 24 }}>
                Coverage across {rtosCovered.join(", ")}.
                {totalHydroDocs > 0 && ` ${totalHydroDocs} hydro-relevant document${totalHydroDocs === 1 ? "" : "s"} attached.`}
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 32 }}>
              <div style={{ padding: 14, background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 4 }}>Meetings</div>
                <div style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{items.length}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>hydro-relevant</div>
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

            {items.length > 0 && (
              <>
                <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.06em", fontWeight: 600, marginBottom: 12 }}>
                  Top items to read
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {items.slice(0, 5).map(e => (
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
