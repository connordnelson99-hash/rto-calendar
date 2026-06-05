// export-modal.jsx — "Export data" dialog. Lets the user narrow the hydro
// corpus by a date range (dual-thumb timeline) and by RTO/ISO (checkboxes),
// shows a live download-size estimate, and packages the filtered JSON + CSV
// with a generated CLAUDE.md into a single .zip.

const _fmtBytes = (n) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
};
const _fmtDate = (iso) =>
  new Date(iso + "T12:00:00").toLocaleDateString("en-US",
    { month: "short", day: "numeric", year: "numeric" });
const _addDays = (iso, days) => {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};
const _daysBetween = (a, b) =>
  Math.round((new Date(b + "T12:00:00") - new Date(a + "T12:00:00")) / 86400000);

const ExportModal = ({ open, onClose }) => {
  const enc = React.useMemo(() => new TextEncoder(), []);
  const [corpus, setCorpus] = React.useState(window.HYDRO_CORPUS || null);
  const [loadErr, setLoadErr] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (open && !corpus && !loadErr) {
      window.loadHydroCorpus().then(setCorpus)
        .catch((e) => setLoadErr(e.message || "Failed to load corpus"));
    }
  }, [open, corpus, loadErr]);

  // Bounds, RTO list, and per-record byte sizes (computed once per corpus).
  const meta = React.useMemo(() => {
    if (!corpus || !corpus.length) return null;
    const dates = corpus.map((r) => r.meeting_date).filter(Boolean).sort();
    const minDate = dates[0];
    const maxDate = dates[dates.length - 1];
    const span = Math.max(1, _daysBetween(minDate, maxDate));
    const rtoCounts = {};
    for (const r of corpus) rtoCounts[r.rto] = (rtoCounts[r.rto] || 0) + 1;
    const rtos = Object.keys(rtoCounts).sort();
    const sized = corpus.map((r) => ({
      r,
      jb: enc.encode(JSON.stringify(r)).length,
      cb: enc.encode(window.corpusRowToCsv(r)).length + 2, // + CRLF
    }));
    const csvHeaderBytes = enc.encode(window.CORPUS_CSV_HEADER).length + 3;
    return { minDate, maxDate, span, rtoCounts, rtos, sized, csvHeaderBytes };
  }, [corpus, enc]);

  const [startIdx, setStartIdx] = React.useState(0);
  const [endIdx, setEndIdx] = React.useState(0);
  const [selRtos, setSelRtos] = React.useState(null); // Set<string>

  // Reset controls to "everything" whenever the modal opens or data arrives.
  React.useEffect(() => {
    if (open && meta) {
      setStartIdx(0);
      setEndIdx(meta.span);
      setSelRtos(new Set(meta.rtos));
    }
  }, [open, meta]);

  if (!open) return null;

  const startDate = meta ? _addDays(meta.minDate, startIdx) : null;
  const endDate = meta ? _addDays(meta.minDate, endIdx) : null;

  // Filtered set + size estimate (cheap O(n) sum over precomputed sizes).
  const sel = selRtos || new Set();
  const filtered = meta
    ? meta.sized.filter((x) =>
        sel.has(x.r.rto) &&
        x.r.meeting_date && x.r.meeting_date >= startDate && x.r.meeting_date <= endDate)
    : [];
  const count = filtered.length;
  let jsonBytes = 2, csvBytes = meta ? meta.csvHeaderBytes : 0;
  for (const x of filtered) { jsonBytes += x.jb + 1; csvBytes += x.cb; }
  const estBytes = count ? jsonBytes + csvBytes + 2600 /* readme+zip overhead */ : 0;

  const pct = (i) => (meta ? (i / meta.span) * 100 : 0);

  const toggleRto = (rto) => {
    const next = new Set(sel);
    next.has(rto) ? next.delete(rto) : next.add(rto);
    setSelRtos(next);
  };
  const allSelected = meta && sel.size === meta.rtos.length;
  const setAll = (on) => setSelRtos(on ? new Set(meta.rtos) : new Set());

  const onDownload = () => {
    if (!count || busy) return;
    setBusy(true);
    try {
      const records = filtered.map((x) => x.r);
      const byRto = {};
      for (const r of records) byRto[r.rto] = (byRto[r.rto] || 0) + 1;
      const filterNote =
        `Filtered to meetings ${_fmtDate(startDate)} – ${_fmtDate(endDate)}; ` +
        `markets: ${Array.from(sel).sort().join(", ")}.`;
      const readme = window.buildCorpusReadme({
        dateStr: new Date().toISOString().slice(0, 10),
        total: records.length, byRto, filterNote,
      });
      const blob = window.buildZipBlob([
        { name: "CLAUDE.md", content: readme },
        { name: "rto_hydro_corpus.json", content: JSON.stringify(records) },
        { name: "rto_hydro_corpus.csv", content: window.corpusToCsv(records) },
      ]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `nha-rto-hydro-corpus-${startDate}_${endDate}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  };

  const btn = (disabled) => ({
    display: "flex", alignItems: "center", gap: 6, padding: "8px 14px",
    background: disabled ? "transparent" : "var(--hydro)",
    color: disabled ? "var(--text-soft)" : "#fff",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--hydro)"),
    borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 13, fontWeight: 600, opacity: disabled ? 0.6 : 1,
  });

  return (
    <>
      <div className="reader-overlay open" onClick={onClose} />
      <div className="reader" style={{ position: "fixed", top: "12vh", bottom: "auto", left: "50%", right: "auto", transform: "translateX(-50%)", width: 560, maxWidth: "92vw", maxHeight: "76vh", display: "grid", gridTemplateRows: "48px 1fr" }}>
        <div className="reader-head">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 28, height: 28, borderRadius: 6, background: "var(--hydro)", color: "#fff", display: "grid", placeItems: "center" }}>
              <Icon name="download" size={14} />
            </div>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Export data</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Choose a range and markets to package for Claude
              </div>
            </div>
          </div>
          <div style={{ flex: 1 }} />
          <button className="close-btn" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>

        <div style={{ overflowY: "auto", padding: "20px 24px", background: "var(--bg)" }}>
          {loadErr && (
            <div style={{ color: "var(--danger, #b91c1c)", fontSize: 13 }}>
              Couldn’t load the corpus: {loadErr}
            </div>
          )}
          {!loadErr && !meta && (
            <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Loading corpus…</div>
          )}

          {meta && (
            <>
              {/* Date range */}
              <div style={{ marginBottom: 22 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, color: "var(--text-muted)" }}>Date range</div>
                  <div style={{ fontSize: 13, fontWeight: 500, fontVariantNumeric: "tabular-nums" }}>
                    {_fmtDate(startDate)} — {_fmtDate(endDate)}
                  </div>
                </div>
                <div className="tl-range">
                  <div className="tl-track" />
                  <div className="tl-fill" style={{ left: pct(startIdx) + "%", width: (pct(endIdx) - pct(startIdx)) + "%" }} />
                  <input type="range" min={0} max={meta.span} value={startIdx}
                    onChange={(e) => setStartIdx(Math.min(+e.target.value, endIdx))} />
                  <input type="range" min={0} max={meta.span} value={endIdx}
                    onChange={(e) => setEndIdx(Math.max(+e.target.value, startIdx))} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-soft)" }}>
                  <span>{_fmtDate(meta.minDate)}</span>
                  <span>{_fmtDate(meta.maxDate)}</span>
                </div>
              </div>

              {/* Markets */}
              <div style={{ marginBottom: 22 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600, color: "var(--text-muted)" }}>Markets</div>
                  <button onClick={() => setAll(!allSelected)}
                    style={{ background: "transparent", border: "none", color: "var(--hydro-strong, var(--hydro))", fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                    {allSelected ? "Clear all" : "Select all"}
                  </button>
                </div>
                <div className="export-rto-grid">
                  {meta.rtos.map((rto) => {
                    const m = (window.RTO_META && window.RTO_META[rto]) || {};
                    return (
                      <label key={rto} className="export-rto-item">
                        <input type="checkbox" checked={sel.has(rto)} onChange={() => toggleRto(rto)} />
                        <span style={{ width: 9, height: 9, borderRadius: 2, background: m.color || "var(--text-soft)" }} />
                        <span style={{ fontWeight: 500 }}>{m.label || rto}</span>
                        <span style={{ marginLeft: "auto", color: "var(--text-soft)", fontVariantNumeric: "tabular-nums" }}>
                          {meta.rtoCounts[rto]}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Summary + action */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "14px 16px", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 8 }}>
                <div>
                  <div style={{ fontSize: 22, fontWeight: 600, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>
                    {count.toLocaleString()} <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-muted)" }}>document{count === 1 ? "" : "s"}</span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                    ≈ {_fmtBytes(estBytes)} zip · JSON + CSV + CLAUDE.md
                  </div>
                </div>
                <button style={btn(!count || busy)} onClick={onDownload} disabled={!count || busy}>
                  <Icon name="download" size={14} />
                  {busy ? "Preparing…" : "Download .zip"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
};

window.ExportModal = ExportModal;
