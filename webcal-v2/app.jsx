// app.jsx — main App shell, state, keyboard nav.
// Loads data asynchronously from rto_events_with_docs.json on mount.

const { useState, useEffect, useMemo, useCallback } = React;

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

  const [filters, setFilters] = useState({ view: "all", rto: "all", q: "" });
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
      if (filters.view === "today" && (e.date < data.today || e.date > data.weekEnd)) return false;
      if (filters.rto !== "all" && e.rto !== filters.rto) return false;
      if (filters.q) {
        const q = filters.q.toLowerCase();
        const hay = (e.title + " " + (e.committee || "") + " " + e.rto).toLowerCase();
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
  }, [monthCursor, selectedId, readerDoc, digestOpen]);

  if (!data) return <LoadingScreen error={loadError}/>;

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <div className="brand-logo">M</div>
          <span>Markets Calendar</span>
          <span style={{ fontSize: 11, color: "var(--text-soft)", fontWeight: 400, marginLeft: 4, padding: "2px 6px", border: "1px solid var(--border)", borderRadius: 3 }}>NHA</span>
        </div>
        <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
          <div className="topbar-search">
            <Icon name="search" size={14} className="search-icon"/>
            <input
              placeholder="Search meetings, committees, documents…"
              value={filters.q}
              onChange={e => setFilters({...filters, q: e.target.value})}/>
            <span className="kbd">⌘K</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="icon-btn has-badge" title="Morning digest" onClick={() => setDigestOpen(true)}>
            <Icon name="bell" size={16}/>
          </button>
          <button className="icon-btn" title="Settings"><Icon name="settings" size={16}/></button>
          <div className="avatar">CN</div>
        </div>
      </div>

      <div className="app-body">
        <Sidebar filters={filters} setFilters={setFilters} events={data.events}
                 today={data.today} weekEnd={data.weekEnd}
                 digestCount={data.digestItems.length}/>
        <div className="main">
          <div className="toolbar">
            <div className="toolbar-group">
              <button className={"toolbar-btn" + (calView==="month"?" active":"")} onClick={()=>setCalView("month")}><Icon name="grid" size={12}/> Month</button>
              <button className={"toolbar-btn" + (calView==="week"?" active":"")} onClick={()=>setCalView("week")}><Icon name="calendar" size={12}/> Week</button>
              <button className={"toolbar-btn" + (calView==="agenda"?" active":"")} onClick={()=>setCalView("agenda")}><Icon name="list" size={12}/> Agenda</button>
            </div>
            {filters.rto !== "all" && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, rto: "all"})}>
                <span className="rto-dot" style={{ background: data.rtoMeta[filters.rto]?.color }}/>
                RTO: {data.rtoMeta[filters.rto]?.label}
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
            {filters.q && (
              <span className="filter-chip active" onClick={() => setFilters({...filters, q: ""})}>
                "{filters.q}"
                <span className="x">×</span>
              </span>
            )}
            <span className="filter-chip"><Icon name="filter" size={11}/> Add filter</span>

            <div className="toolbar-spacer"/>
            <span className="toolbar-meta">
              <strong>{filteredEvents.filter(e=>e.isRelevant).length}</strong> hydro-relevant
              {" "}·{" "}
              {filteredEvents.length} of {data.events.length} meetings
            </span>
            <button className="icon-btn" title="Refresh" onClick={() => window.location.reload()}><Icon name="refresh" size={14}/></button>
          </div>

          {calView === "month" && (
            <div className="split">
              <CalendarPane
                events={filteredEvents}
                selectedDate={selectedDate}
                onSelectDate={setSelectedDate}
                onSelectEvent={onSelectEvent}
                today={data.today}
                monthCursor={monthCursor}
                setMonthCursor={setMonthCursor}/>
              <ListPane
                events={filteredEvents}
                selectedId={selectedId}
                onSelect={onSelectEvent}
                today={data.today}
                selectedDate={selectedDate}
                onOpenDigest={() => setDigestOpen(true)}/>
            </div>
          )}
          {calView === "week" && (
            <WeekView
              events={filteredEvents}
              today={data.today}
              onSelectEvent={onSelectEvent}
              anchor={weekAnchor}
              setAnchor={setWeekAnchor}/>
          )}
          {calView === "agenda" && (
            <AgendaView
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
        onOpenDoc={(d, e) => { setReaderDoc(d); setReaderEvent(e); }}/>
      {readerDoc && <DocReader doc={readerDoc} event={readerEvent} onClose={() => setReaderDoc(null)}/>}
      <DigestModal open={digestOpen} onClose={() => setDigestOpen(false)} onOpenEvent={onSelectEvent}/>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
