// list-view.jsx — meeting list grouped by day, with morning digest banner.

const fmtDateHeader = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric" });
};
const fmtWeekday = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "long" });
};
const fmtBanner = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
};

const RtoTag = ({ rto, meta }) => (
  <span className="rto-tag" style={{ background: meta.bg, color: meta.color, border: `1px solid ${meta.color}33` }}>
    <span style={{ width: 5, height: 5, borderRadius: 1, background: meta.color, display: "inline-block" }}/>
    {meta.label}
  </span>
);

const MeetingRow = ({ event, selected, onSelect }) => {
  const e = event;
  return (
    <div className={"meeting-row" + (selected ? " selected" : "")}
         onClick={() => onSelect(e.id)}>
      <div className="row-time">
        {e.time ? (
          <>
            <div style={{ fontWeight: 600, color: "var(--text)" }}>
              {e.time.split(":")[0]}:{e.time.split(":")[1] || "00"}
            </div>
            <div style={{ fontSize: 10, opacity: 0.8 }}>ET</div>
          </>
        ) : <span style={{ color: "var(--text-soft)" }}>—</span>}
      </div>
      <div className="row-body">
        <div className="row-title-line">
          <RtoTag rto={e.rto} meta={e.rtoMeta}/>
          <span className="row-title">
            {e.title.replace(new RegExp(`^${e.rtoMeta.label}\\s+`, "i"), "")}
          </span>
          {e.isRelevant && (
            <span className="hydro-flag" title="Hydro-relevant">
              <span className="hydro-tri"/> {e.hydroDocCount > 0 ? `${e.hydroDocCount} doc${e.hydroDocCount>1?"s":""}` : "Relevant"}
            </span>
          )}
        </div>
        <div className="row-meta-line">
          {e.committee && <><span className="committee">{e.committee}</span><span className="dot">·</span></>}
          <span>{e.documents.length} document{e.documents.length === 1 ? "" : "s"}</span>
        </div>
        {e.meetingHydroRelevant && e.meetingHydroReason && (
          <div className="row-reason">{e.meetingHydroReason}</div>
        )}
        {e.documents.length > 0 && (
          <div className="row-docs">
            {e.documents.slice(0, 4).map(d => (
              <span key={d.id} className={"row-doc" + (d.hydro_relevant ? " hydro" : "")}>
                <Icon name="file" size={10} className="row-doc-icon"/>
                <span style={{ textTransform: "capitalize" }}>{d.type}</span>
              </span>
            ))}
            {e.documents.length > 4 && (
              <span className="row-doc">+{e.documents.length - 4}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

const DigestBanner = ({ items, today, onClick }) => {
  const hydroCount = items.filter(e => e.isRelevant).length;
  const newDocCount = items.reduce((sum, e) => sum + e.hydroDocCount, 0);
  if (items.length === 0) return null;
  return (
    <div className="digest" onClick={onClick} style={{ cursor: "pointer" }}>
      <div className="digest-icon"><Icon name="sparkle" size={16}/></div>
      <div className="digest-body">
        <div className="digest-title">
          Morning digest · {fmtBanner(today)}
        </div>
        <div className="digest-text">
          <strong>{hydroCount} hydro-relevant</strong> meeting{hydroCount === 1 ? "" : "s"} this week
          {newDocCount > 0 && <> · <strong>{newDocCount} hydro-relevant doc{newDocCount === 1 ? "" : "s"}</strong> attached</>}
        </div>
      </div>
      <button className="btn">
        <Icon name="arrowRight" size={14}/>
        Open digest
      </button>
    </div>
  );
};

const ListPane = ({ events, selectedId, onSelect, today, selectedDate, onOpenDigest }) => {
  const grouped = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      if (!m[e.date]) m[e.date] = [];
      m[e.date].push(e);
    }
    const dates = Object.keys(m).sort();
    return dates.map(date => ({
      date,
      events: m[date].sort((a, b) => {
        if (a.isRelevant !== b.isRelevant) return b.isRelevant - a.isRelevant;
        return (a.time || "99:99").localeCompare(b.time || "99:99");
      })
    }));
  }, [events]);

  const scrollRef = React.useRef(null);
  React.useEffect(() => {
    if (selectedDate && scrollRef.current) {
      const el = scrollRef.current.querySelector(`[data-date="${selectedDate}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [selectedDate]);

  React.useEffect(() => {
    if (selectedId && scrollRef.current) {
      const el = scrollRef.current.querySelector(`[data-event="${selectedId}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [selectedId]);

  const totalHydro = events.filter(e => e.isRelevant).length;
  const digestItems = window.MARKETS_DATA.digestItems;

  return (
    <div className="list-pane">
      <div className="list-header">
        <div className="list-title">Meetings</div>
        <div className="list-meta">
          <span style={{ color: "var(--hydro)", fontWeight: 600 }}>{totalHydro}</span>
          {" "}hydro-relevant · {events.length} total
        </div>
        <div style={{flex: 1}}/>
      </div>
      <DigestBanner items={digestItems} today={today} onClick={onOpenDigest}/>
      <div className="list-scroll" ref={scrollRef}>
        {grouped.length === 0 && (
          <div className="empty">
            <div className="empty-icon"><Icon name="calendar" size={20}/></div>
            <div style={{ fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>No meetings match these filters</div>
            <div style={{ fontSize: "var(--fs-sm)" }}>Try clearing filters or expanding the date range</div>
          </div>
        )}
        {grouped.map(g => {
          const isToday = g.date === today;
          return (
            <div key={g.date} className="day-section" data-date={g.date}>
              <div className="day-section-header">
                <span className="day-section-date">{fmtDateHeader(g.date)}</span>
                <span className="day-section-day">{fmtWeekday(g.date)}</span>
                {isToday && <span className="day-section-today">Today</span>}
                <span className="day-section-count">{g.events.length} meeting{g.events.length===1?"":"s"}</span>
              </div>
              {g.events.map(e => (
                <div key={e.id} data-event={e.id}>
                  <MeetingRow event={e} selected={selectedId === e.id} onSelect={onSelect}/>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
};

window.ListPane = ListPane;
window.MeetingRow = MeetingRow;
window.RtoTag = RtoTag;
