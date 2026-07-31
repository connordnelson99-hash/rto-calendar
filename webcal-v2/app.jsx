// app.jsx — main App shell, state, keyboard nav.
// Loads data asynchronously from rto_events_with_docs.json on mount.

const { useState, useEffect, useMemo, useCallback } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "density": "comfortable",
  "hydroSignal": "standard"
}/*EDITMODE-END*/;

function LoadingScreen({ error }) {
  return (
    <div style={{
      display: "grid", placeItems: "center", height: "100vh",
      fontFamily: "var(--font-sans)", color: "var(--text-muted)", fontSize: 13
    }}>
      {error
        ? <div style={{ color: "var(--danger, #DC2626)" }}>Failed to load events: {error}</div>
        : <div>Loading meeting feed…</div>}
    </div>
  );
}

// Toolbar control for the confidence filter — how directly an event's documents
// bear on hydro. Relevance is a deliberately wide gate (a rejected document is
// invisible to every consumer forever), so this is the axis that lets a reader
// narrow to the squarely-applicable without re-tuning the screen.
//
// Hidden only when the whole dataset carries no ratings, so it doesn't occupy
// toolbar space on an export that pre-dates directness. Deliberately NOT tied
// to the current filter subset: a narrow search can leave zero rated events,
// and a control that disappears while its filter is still applied leaves the
// user with an active constraint and nothing to adjust it with.
function ConfidenceControl({ events, hasRatings, shownCount, minRank, setMinRank, meta, stops }) {
  const counts = useMemo(() => {
    // `unratedShown` counts everything the slider can't act on — unrated
    // hydro-relevant events AND events with no hydro-relevant docs at all.
    // Both pass every stop, so both belong in each stop's total; otherwise
    // the per-stop numbers wouldn't reconcile with the "N shown" readout.
    // `unratedRelevant` is narrower and only describes the ratings gap.
    const m = { rated: 0, unratedShown: 0, unratedRelevant: 0, byRank: {} };
    for (const e of events) {
      const info = meta?.[e.directness];
      if (info) {
        m.rated++;
        m.byRank[info.rank] = (m.byRank[info.rank] || 0) + 1;
      } else {
        m.unratedShown++;
        if (e.isRelevant) m.unratedRelevant++;
      }
    }
    return m;
  }, [events, meta]);

  if (!stops?.length || !hasRatings) return null;

  const active = stops[minRank - 1];
  const countAt = (rank) => counts.unratedShown + Object.entries(counts.byRank)
    .reduce((n, [r, c]) => n + (Number(r) >= rank ? c : 0), 0);

  const tip = [
    "How directly an event's documents bear on hydro.",
    ...stops.map(s => `${s.label}: ${s.hint} (${countAt(s.minRank)} shown)`),
    counts.unratedRelevant > 0
      ? `${counts.unratedRelevant} hydro-relevant item${counts.unratedRelevant === 1 ? "" : "s"} screened before ratings existed — always shown.`
      : null,
  ].filter(Boolean).join("\n");

  return (
    <div className="toolbar-conf" title={tip}>
      <span className="toolbar-conf-label">Confidence</span>
      <input type="range" min="1" max={stops.length} step="1" value={minRank}
             aria-label="Minimum confidence that a meeting applies to hydro"
             onChange={e => setMinRank(Number(e.target.value))}/>
      <span className="toolbar-conf-readout">
        <strong>{active?.label}</strong>
        <span className="toolbar-conf-count">{shownCount} shown</span>
      </span>
    </div>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);

  // `minRank` is the confidence filter: 1 = All, 2 = Precedent+, 3 = Direct
  // only. See DIRECTNESS_STOPS in data.js.
  const [filters, setFilters] = useState({ view: "all", rto: "all", topic: "all", minRank: 1, q: "", facet: null });
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDate, setSelectedDate] = useState(null);
  const [monthCursor, setMonthCursor] = useState(() => {
    const d = new Date();
    return [d.getFullYear(), d.getMonth()];
  });
  const [calView, setCalView] = useState("month"); // month | week | agenda
  const [weekAnchor, setWeekAnchor] = useState(null);
  const [readerDoc, setReaderDoc] = useState(null);
  const [readerEvent, setReaderEvent] = useState(null);
  const [digestOpen, setDigestOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const theme = tweaks.theme;
  const setTheme = (next) => setTweak("theme",
    typeof next === "function" ? next(tweaks.theme) : next);

  useEffect(() => {
    document.body.dataset.theme = tweaks.theme;
    document.body.dataset.density = tweaks.density;
    document.body.dataset.hydroSignal = tweaks.hydroSignal;
  }, [tweaks.theme, tweaks.density, tweaks.hydroSignal]);

  // Load events on mount
  useEffect(() => {
    window.loadMarketsData()
      .then(d => {
        setData(d);
        setSelectedDate(d.today);
        setWeekAnchor(d.today);
      })
      .catch(err => {
        console.error("Failed to load market data:", err);
        setLoadError(err.message || String(err));
      });
  }, []);

  // Everything except the confidence filter. Kept separate so the confidence
  // control can report how many events each of its stops would show *under the
  // other active filters* — with RTO=CAISO set, the useful number is how many
  // CAISO events survive each stop, not how many across all markets.
  const preConfidenceEvents = useMemo(() => {
    if (!data) return [];
    return data.events.filter(e => {
      if (filters.view === "hydro" && !e.isRelevant) return false;
      if (filters.view === "initiative" && !e.hasIssues) return false;
      if (filters.view === "today" && (e.date < data.today || e.date > data.weekEnd)) return false;
      if (filters.rto !== "all" && e.rto !== filters.rto) return false;
      if (filters.topic !== "all" && !(e.topics || []).includes(filters.topic)) return false;
      if (filters.facet && !matchesFacet(e, filters.facet)) return false;
      if (filters.q) {
        const q = filters.q.toLowerCase();
        const issueText = (e.issues || []).map(i => `${i.title || ""} ${i.name || ""} ${i.native_id || ""}`).join(" ");
        const stakeholderText = (e.documents || [])
          .flatMap(d => d.stakeholders || [])
          .map(s => `${s.name || ""} ${s.entity || ""}`).join(" ");
        const docText = (e.documents || []).map(d => d.title || "").join(" ");
        const hay = (
          e.title + " " + (e.committee || "") + " " + e.rto +
          " " + issueText + " " + stakeholderText + " " + docText
        ).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [data, filters]);

  // Confidence filter. Unrated events (every doc screened before directness
  // existed) pass at every stop: they carry no rating to judge, and dropping
  // them would silently hide most of the archive rather than narrowing it. The
  // control states how many are unrated so this is visible, not a surprise.
  const filteredEvents = useMemo(() => {
    if (filters.minRank <= 1 || !data) return preConfidenceEvents;
    return preConfidenceEvents.filter(e => {
      const meta = data.directnessMeta[e.directness];
      return !meta || meta.rank >= filters.minRank;
    });
  }, [data, preConfidenceEvents, filters.minRank]);

  // Whether directness exists in this export at all — gates the toolbar control.
  const hasRatings = useMemo(
    () => !!data && data.events.some(e => e.directness),
    [data]);

  const selectedEvent = data && data.events.find(e => e.id === selectedId);

  const onSelectEvent = useCallback((id) => {
    setSelectedId(id);
    if (!data) return;
    const ev = data.events.find(e => e.id === id);
    if (ev) setSelectedDate(ev.date);
  }, [data]);

  // keyboard: arrows for month nav, esc to close
  useEffect(() => {
    const handler = (ev) => {
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA") return;
      if (ev.key === "Escape") {
        if (readerDoc) setReaderDoc(null);
        else if (settingsOpen) setSettingsOpen(false);
        else if (exportOpen) setExportOpen(false);
        else if (digestOpen) setDigestOpen(false);
        else if (selectedId) setSelectedId(null);
      } else if (ev.key === "ArrowLeft") {
        const [y, m] = monthCursor;
        if (m === 0) setMonthCursor([y - 1, 11]); else setMonthCursor([y, m - 1]);
      } else if (ev.key === "ArrowRight") {
        const [y, m] = monthCursor;
        if (m === 11) setMonthCursor([y + 1, 0]); else setMonthCursor([y, m + 1]);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [monthCursor, selectedId, readerDoc, digestOpen, exportOpen, settingsOpen]);

  if (!data) return <LoadingScreen error={loadError}/>;

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <img src="assets/NHA-Logo.png" alt="NHA" className="brand-logo-img"/>
          <span>RTO/ISO Calendar</span>
        </div>
        <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
          <SearchBox data={data} filters={filters} setFilters={setFilters}
                     onSelectEvent={onSelectEvent}/>
        </div>
        <div className="topbar-actions">
          <button className="btn" title="Open weekly digest" onClick={() => setDigestOpen(true)}>
            <Icon name="sparkle" size={14}/>
            Weekly digest
          </button>
          <button className="btn" onClick={() => setExportOpen(true)}
            title="Choose a date range and markets, then download the hydro corpus (JSON + CSV + CLAUDE.md) as a zip for analysis in Claude">
            <Icon name="download" size={14}/>
            Export data
          </button>
          <button className="icon-btn" title="About" onClick={() => setSettingsOpen(true)}><Icon name="settings" size={16}/></button>
        </div>
      </div>

      <div className="app-body">
        <Sidebar filters={filters} setFilters={setFilters} events={data.events}
                 today={data.today} weekEnd={data.weekEnd}
                 digestCount={data.digestItems.length}
                 theme={theme}
                 onToggleTheme={() => setTheme(t => t === "dark" ? "light" : "dark")}/>
        <div className="main">
          <div className="toolbar">
            <div className="toolbar-group">
              <button className={"toolbar-btn" + (calView==="month"?" active":"")} onClick={()=>setCalView("month")}><Icon name="grid" size={12}/> Month</button>
              <button className={"toolbar-btn" + (calView==="week"?" active":"")} onClick={()=>setCalView("week")}><Icon name="calendar" size={12}/> Week</button>
              <button className={"toolbar-btn" + (calView==="list"?" active":"")} onClick={()=>setCalView("list")}><Icon name="list" size={12}/> List</button>
            </div>
            {filters.rto !== "all" && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, rto: "all"})}>
                <span className="rto-dot" style={{ background: data.rtoMeta[filters.rto]?.color }}/>
                RTO: {data.rtoMeta[filters.rto]?.label}
                <span className="x">×</span>
              </span>
            )}
            {filters.topic !== "all" && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, topic: "all"})}>
                <Icon name="tag" size={11}/>
                {data.topicMeta?.[filters.topic]?.label || filters.topic}
                <span className="x">×</span>
              </span>
            )}
            {filters.minRank > 1 && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, minRank: 1})}>
                <Icon name="target" size={11}/>
                {data.directnessStops?.[filters.minRank - 1]?.label} or stronger
                <span className="x">×</span>
              </span>
            )}
            {filters.view === "hydro" && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, view: "all"})}>
                <span className="hydro-tri"/>
                Hydro-relevant only
                <span className="x">×</span>
              </span>
            )}
            {filters.view === "initiative" && (
              <span className="filter-chip active initiative" onClick={() => setFilters({...filters, view: "all"})}>
                <Icon name="target" size={11}/>
                Initiative-linked only
                <span className="x">×</span>
              </span>
            )}
            {filters.facet && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, facet: null})}>
                <Icon name={FACET_ICON[filters.facet.kind] || "filter"} size={11}/>
                {filters.facet.label}
                <span className="x">×</span>
              </span>
            )}
            {filters.q && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, q: ""})}>
                "{filters.q}"
                <span className="x">×</span>
              </span>
            )}
            <div className="toolbar-spacer"/>
            <ConfidenceControl
              events={preConfidenceEvents}
              hasRatings={hasRatings}
              shownCount={filteredEvents.length}
              minRank={filters.minRank}
              setMinRank={(minRank) => setFilters({ ...filters, minRank })}
              meta={data.directnessMeta}
              stops={data.directnessStops}/>
            <button className="icon-btn" title="Refresh" onClick={() => window.location.reload()}><Icon name="refresh" size={14}/></button>
          </div>

          {calView === "month" && (
            <CalendarPane
              events={filteredEvents}
              selectedDate={selectedDate}
              onSelectDate={setSelectedDate}
              onSelectEvent={onSelectEvent}
              today={data.today}
              monthCursor={monthCursor}
              setMonthCursor={setMonthCursor}/>
          )}
          {calView === "list" && (
            <ListPane
              events={filteredEvents}
              selectedId={selectedId}
              onSelect={onSelectEvent}
              today={data.today}
              selectedDate={selectedDate}/>
          )}
          {calView === "week" && (
            <WeekView
              events={filteredEvents}
              today={data.today}
              onSelectEvent={onSelectEvent}
              anchor={weekAnchor}
              setAnchor={setWeekAnchor}/>
          )}
        </div>
      </div>

      <DetailPane event={selectedEvent}
        onClose={() => setSelectedId(null)}
        onOpenDoc={() => {}}/>
      <DigestModal open={digestOpen} onClose={() => setDigestOpen(false)} onOpenEvent={onSelectEvent}/>
      <ExportModal open={exportOpen} onClose={() => setExportOpen(false)}/>

      {settingsOpen && (
        <div className="settings-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
            <button className="settings-close" onClick={() => setSettingsOpen(false)} aria-label="Close">
              <Icon name="x" size={16}/>
            </button>
            <img className="settings-headshot" src="assets/connor-headshot.png" alt="Connor Nelson"/>
            <div className="settings-credit-eyebrow">Developed by</div>
            <div className="settings-credit-name">Connor Nelson</div>
            <div className="settings-credit-meta">RTO/ISO Calendar &middot; National Hydropower Association</div>
          </div>
        </div>
      )}

      <TweaksPanel title="Tweaks">
        <TweakSection title="Palette">
          <TweakRadio label="Theme" value={tweaks.theme} onChange={(v) => setTweak("theme", v)}
            options={[
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
              { value: "hydro", label: "Hydro" },
              { value: "terminal", label: "Terminal" }
            ]}/>
        </TweakSection>
        <TweakSection title="Rhythm">
          <TweakRadio label="Density" value={tweaks.density} onChange={(v) => setTweak("density", v)}
            options={[
              { value: "comfortable", label: "Comfort" },
              { value: "compact", label: "Compact" },
              { value: "ultra", label: "Ultra" }
            ]}/>
        </TweakSection>
        <TweakSection title="Hydro signal" subtitle="How loudly relevant items announce themselves">
          <TweakRadio label="Intensity" value={tweaks.hydroSignal} onChange={(v) => setTweak("hydroSignal", v)}
            options={[
              { value: "quiet", label: "Quiet" },
              { value: "standard", label: "Standard" },
              { value: "loud", label: "Loud" }
            ]}/>
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
