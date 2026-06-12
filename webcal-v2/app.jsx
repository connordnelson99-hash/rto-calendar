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

function App() {
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);

  const [filters, setFilters] = useState({ view: "all", rto: "all", topic: "all", q: "" });
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

  // Filter events
  const filteredEvents = useMemo(() => {
    if (!data) return [];
    return data.events.filter(e => {
      if (filters.view === "hydro" && !e.isRelevant) return false;
      if (filters.view === "initiative" && !e.hasIssues) return false;
      if (filters.view === "today" && (e.date < data.today || e.date > data.weekEnd)) return false;
      if (filters.rto !== "all" && e.rto !== filters.rto) return false;
      if (filters.topic !== "all" && !(e.topics || []).includes(filters.topic)) return false;
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
          <div className="topbar-search">
            <Icon name="search" size={14} className="search-icon"/>
            <input
              placeholder="Search meetings, committees, initiatives, stakeholders…"
              value={filters.q}
              onChange={e => setFilters({...filters, q: e.target.value})}/>
            <span className="kbd">⌘K</span>
          </div>
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
            {filters.q && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, q: ""})}>
                "{filters.q}"
                <span className="x">×</span>
              </span>
            )}
            <div className="toolbar-spacer"/>
            <span className="toolbar-meta">
              <strong>{filteredEvents.filter(e=>e.isRelevant).length}</strong> hydro-relevant
              {" "}·{" "}
              <strong className="initiative-count">{filteredEvents.filter(e=>e.hasIssues).length}</strong> initiative-linked
              {" "}·{" "}
              {filteredEvents.length} of {data.events.length} meetings
            </span>
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
              selectedDate={selectedDate}
              onOpenDigest={() => setDigestOpen(true)}/>
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
