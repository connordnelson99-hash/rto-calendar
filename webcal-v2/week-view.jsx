// week-view.jsx — week timeline with hour gutter, days as columns, events placed by time
// agenda-view.jsx is appended below as well

const WEEKDAYS_FULL = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const WEEKDAYS_LONG = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

function weekStart(iso) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() - d.getDay());
  return d;
}
function isoOf(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,"0");
  const day = String(d.getDate()).padStart(2,"0");
  return `${y}-${m}-${day}`;
}
function addDays(d, n) {
  const r = new Date(d); r.setDate(d.getDate()+n); return r;
}
function timeToMinutes(t) {
  if (!t || !t.includes(":")) return null;
  const [h,m] = t.split(":").map(Number);
  return h*60 + (m||0);
}

const WeekView = ({ events, today, onSelectEvent, anchor, setAnchor }) => {
  const start = weekStart(anchor);
  const days = Array.from({length: 7}, (_, i) => addDays(start, i));
  const dayIsos = days.map(isoOf);

  // Hour rows: 6 AM through 10 PM (16h) — covers every meeting time in real data
  const HOUR_START = 6, HOUR_END = 22;
  const HOURS = Array.from({length: HOUR_END - HOUR_START + 1}, (_, i) => HOUR_START + i);
  const HOUR_PX = 56; // height per hour

  const eventsByDay = React.useMemo(() => {
    const m = {};
    for (const iso of dayIsos) m[iso] = [];
    for (const e of events) {
      if (m[e.date]) m[e.date].push(e);
    }
    for (const iso in m) {
      m[iso].sort((a,b) => (timeToMinutes(a.time)||9999) - (timeToMinutes(b.time)||9999));
    }
    return m;
  }, [events, dayIsos.join(",")]);

  const totalHydro = dayIsos.reduce((acc, iso) => acc + eventsByDay[iso].filter(e=>e.isRelevant).length, 0);
  const totalEvents = dayIsos.reduce((acc, iso) => acc + eventsByDay[iso].length, 0);

  const prev = () => setAnchor(isoOf(addDays(start, -7)));
  const next = () => setAnchor(isoOf(addDays(start, 7)));
  const goToday = () => setAnchor(today);

  const fmtRange = () => {
    const end = addDays(start, 6);
    const sameMonth = start.getMonth() === end.getMonth();
    const f = (d) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    if (sameMonth) {
      return `${start.toLocaleDateString("en-US",{month:"long"})} ${start.getDate()} – ${end.getDate()}, ${end.getFullYear()}`;
    }
    return `${f(start)} – ${f(end)}, ${end.getFullYear()}`;
  };

  // Layout: events placed by time. If two share a slot, they side-by-side within day column.
  const layoutDay = (dayEvents) => {
    // Greedy column assignment by overlap (assume 60-min default duration)
    const items = dayEvents.map(e => {
      const start = timeToMinutes(e.time);
      return {
        event: e,
        startMin: start ?? null,
        endMin: start != null ? start + 60 : null,
        col: 0
      };
    });
    // assign columns
    const timed = items.filter(i => i.startMin != null).sort((a,b) => a.startMin - b.startMin);
    const lanes = [];
    for (const item of timed) {
      let placed = false;
      for (let i = 0; i < lanes.length; i++) {
        if (lanes[i] <= item.startMin) {
          item.col = i;
          lanes[i] = item.endMin;
          placed = true;
          break;
        }
      }
      if (!placed) {
        item.col = lanes.length;
        lanes.push(item.endMin);
      }
    }
    const cols = Math.max(1, lanes.length);
    return { items, cols };
  };

  return (
    <div className="calendar-pane">
      <div className="cal-header">
        <div className="cal-title">{fmtRange()}</div>
        <div className="cal-nav">
          <button className="cal-nav-btn" onClick={prev}><Icon name="chevronLeft" size={14}/></button>
          <button className="cal-nav-btn" onClick={next}><Icon name="chevronRight" size={14}/></button>
        </div>
        <button className="cal-today-btn" onClick={goToday}>This week</button>
        <div style={{flex: 1}}/>
        <span className="toolbar-meta">
          <strong>{totalHydro}</strong> hydro · {totalEvents} meetings
        </span>
      </div>

      <div className="week-wrap">
        <div className="week-day-headers">
          <div className="week-gutter-spacer"/>
          {days.map((d, i) => {
            const iso = dayIsos[i];
            const isToday = iso === today;
            return (
              <div key={iso} className={"week-day-head" + (isToday ? " today" : "")}>
                <div className="week-day-name">{WEEKDAYS_FULL[i]}</div>
                <div className="week-day-num">{d.getDate()}</div>
              </div>
            );
          })}
        </div>

        <div className="week-grid" style={{ "--hour-px": HOUR_PX + "px" }}>
          <div className="week-gutter">
            {HOURS.map(h => (
              <div key={h} className="week-hour-label">
                <span>{h === 12 ? "12 PM" : h > 12 ? `${h-12} PM` : `${h} AM`}</span>
              </div>
            ))}
          </div>

          {days.map((d, i) => {
            const iso = dayIsos[i];
            const dayEvents = eventsByDay[iso];
            const { items, cols } = layoutDay(dayEvents);
            const isToday = iso === today;

            // current-time line position (only for today, viewer's local clock)
            const now = new Date();
            const nowMins = now.getHours()*60 + now.getMinutes();
            const nowOffset = ((nowMins - HOUR_START*60) / 60) * HOUR_PX;

            return (
              <div key={iso} className={"week-day-col" + (isToday ? " today" : "")}>
                {HOURS.map(h => <div key={h} className="week-hour-cell"/>)}
                {isToday && nowOffset >= 0 && nowOffset <= HOURS.length * HOUR_PX && (
                  <div className="week-now-line" style={{ top: nowOffset }}>
                    <div className="week-now-dot"/>
                  </div>
                )}
                {items.map((item, idx) => {
                  const e = item.event;
                  const top = item.startMin != null
                    ? ((item.startMin - HOUR_START*60) / 60) * HOUR_PX
                    : null;
                  const heightPx = HOUR_PX - 4;
                  const widthPct = 100 / cols;
                  if (top == null) return null;
                  return (
                    <div key={e.id}
                         className={"week-event" + (e.isRelevant ? " hydro" : "")}
                         style={{
                           top: top,
                           left: `calc(${item.col * widthPct}% + 2px)`,
                           width: `calc(${widthPct}% - 4px)`,
                           height: heightPx,
                           borderLeftColor: e.rtoMeta.color,
                           background: e.isRelevant ? undefined : e.rtoMeta.bg
                         }}
                         onClick={() => onSelectEvent(e.id)}
                         title={
                           `${e.rtoMeta.label}: ${e.title}` +
                           (e.timeFmt ? `\n${e.timeFmt}` : "") +
                           (e.sourceTimeFmt ? `\n(originally ${e.sourceTimeFmt})` : "")
                         }>
                      <div className="week-event-title">
                        <span className="rto-tag" style={{ background: e.rtoMeta.color, color: "#fff", border: "none", fontSize: 9, padding: "0 4px" }}>
                          {e.rtoMeta.label}
                        </span>
                        {" "}
                        {e.title.replace(new RegExp(`^${e.rtoMeta.label}\\s+`, "i"), "").replace(/\s+Meeting$/, "")}
                      </div>
                    </div>
                  );
                })}

                {/* All-day / no-time events: pin to top of column */}
                {dayEvents.filter(e => timeToMinutes(e.time) == null).length > 0 && (
                  <div className="week-allday">
                    {dayEvents.filter(e => timeToMinutes(e.time) == null).map(e => (
                      <div key={e.id}
                           className={"week-allday-event" + (e.isRelevant ? " hydro" : "")}
                           onClick={() => onSelectEvent(e.id)}
                           style={{ borderLeftColor: e.rtoMeta.color }}>
                        <span className="rto-tag" style={{ background: e.rtoMeta.color, color: "#fff", border: "none", fontSize: 9, padding: "0 4px" }}>
                          {e.rtoMeta.label}
                        </span>{" "}
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{e.title}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

window.WeekView = WeekView;

// ─── Agenda View ────────────────────────────────────────────────
// Single-column list, chronological. No grid. Designed for triage:
// large date dividers, dense rows, hydro items pop visually.

const AgendaView = ({ events, today, onSelectEvent, anchor, setAnchor }) => {
  // Group events by date
  const grouped = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      if (!m[e.date]) m[e.date] = [];
      m[e.date].push(e);
    }
    const dates = Object.keys(m).sort();
    return dates.map(date => ({
      date,
      events: m[date].sort((a,b) => (timeToMinutes(a.time)||9999) - (timeToMinutes(b.time)||9999))
    }));
  }, [events]);

  const totalHydro = events.filter(e => e.isRelevant).length;

  // Group by week for stats sidebar
  const weeks = React.useMemo(() => {
    const m = {};
    for (const g of grouped) {
      const ws = isoOf(weekStart(g.date));
      if (!m[ws]) m[ws] = { start: ws, events: [], hydroCount: 0 };
      m[ws].events.push(...g.events);
      m[ws].hydroCount += g.events.filter(e => e.isRelevant).length;
    }
    return Object.values(m).sort((a,b) => a.start.localeCompare(b.start));
  }, [grouped]);

  const [scrollTarget, setScrollTarget] = React.useState(null);
  const scrollRef = React.useRef(null);
  React.useEffect(() => {
    if (scrollTarget && scrollRef.current) {
      const el = scrollRef.current.querySelector(`[data-week="${scrollTarget}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [scrollTarget]);

  const fmtAgendaDate = (iso) => {
    const d = new Date(iso + "T12:00:00");
    return {
      day: d.getDate(),
      month: d.toLocaleDateString("en-US", { month: "short" }),
      weekday: d.toLocaleDateString("en-US", { weekday: "long" }),
      year: d.getFullYear(),
      isToday: iso === today,
      isPast: iso < today
    };
  };

  return (
    <div className="agenda-pane">
      <div className="agenda-side">
        <div className="agenda-side-head">
          <div className="agenda-side-title">Schedule overview</div>
          <div className="agenda-side-sub">
            <strong style={{ color: "var(--hydro)" }}>{totalHydro}</strong> hydro-relevant ·{" "}
            {events.length} meetings
          </div>
        </div>
        <div className="agenda-week-list">
          {weeks.map(w => {
            const isCurrent = w.start <= today && today <= isoOf(addDays(new Date(w.start+"T12:00:00"), 6));
            const ws = new Date(w.start+"T12:00:00");
            const we = addDays(ws, 6);
            return (
              <div key={w.start}
                   className={"agenda-week-item" + (isCurrent ? " current" : "")}
                   onClick={() => setScrollTarget(w.start)}>
                <div className="agenda-week-range">
                  {ws.toLocaleDateString("en-US",{month:"short",day:"numeric"})} – {we.toLocaleDateString("en-US",{month:"short",day:"numeric"})}
                </div>
                <div className="agenda-week-stats">
                  <span className="agenda-week-stat">
                    <span style={{ color: "var(--hydro)", fontWeight: 600 }}>{w.hydroCount}</span>
                    <span style={{ color: "var(--text-soft)" }}>hydro</span>
                  </span>
                  <span className="agenda-week-stat">
                    <span style={{ fontWeight: 600 }}>{w.events.length}</span>
                    <span style={{ color: "var(--text-soft)" }}>total</span>
                  </span>
                </div>
                <div className="agenda-week-bar">
                  {Array.from({length: 5}).map((_, i) => {
                    const intensity = Math.min(1, w.events.length / 20);
                    const filled = i < Math.ceil(intensity * 5);
                    return <span key={i} className={"agenda-week-tick" + (filled ? " filled" : "")}/>;
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="agenda-main" ref={scrollRef}>
        {grouped.length === 0 && (
          <div className="empty">
            <div className="empty-icon"><Icon name="calendar" size={20}/></div>
            <div style={{ fontWeight: 500, color: "var(--text)", marginBottom: 4 }}>No meetings match these filters</div>
          </div>
        )}
        {grouped.map(g => {
          const d = fmtAgendaDate(g.date);
          const wkStart = isoOf(weekStart(g.date));
          const hydroN = g.events.filter(e => e.isRelevant).length;
          return (
            <div key={g.date} className="agenda-day" data-week={wkStart}>
              <div className={"agenda-day-stripe" + (d.isPast ? " past" : "") + (d.isToday ? " today" : "")}>
                <div className="agenda-stripe-num">{d.day}</div>
                <div className="agenda-stripe-month">{d.month}</div>
                <div className="agenda-stripe-weekday">{d.weekday}</div>
                {d.isToday && <div className="agenda-stripe-today">Today</div>}
                <div style={{ flex: 1 }}/>
                <div className="agenda-stripe-counts">
                  {hydroN > 0 && (
                    <span className="agenda-stripe-hydro">
                      <span className="hydro-tri"/> {hydroN}
                    </span>
                  )}
                  <span className="agenda-stripe-total">{g.events.length} meeting{g.events.length===1?"":"s"}</span>
                </div>
              </div>
              <div className="agenda-rows">
                {g.events.map(e => (
                  <div key={e.id} className={"agenda-row" + (e.isRelevant ? " hydro" : "")}
                       onClick={() => onSelectEvent(e.id)}>
                    <div className="agenda-row-time"
                         title={e.sourceTimeFmt ? `Originally ${e.sourceTimeFmt}` : null}>
                      {e.timeFmt || "—"}
                    </div>
                    <span className="rto-tag" style={{ background: e.rtoMeta.bg, color: e.rtoMeta.color, border: `1px solid ${e.rtoMeta.color}33` }}>
                      <span style={{ width: 5, height: 5, borderRadius: 1, background: e.rtoMeta.color, display: "inline-block" }}/>
                      {e.rtoMeta.label}
                    </span>
                    <div className="agenda-row-title">
                      {e.title.replace(new RegExp(`^${e.rtoMeta.label}\\s+`, "i"), "")}
                    </div>
                    {e.committee && <div className="agenda-row-committee">{e.committee}</div>}
                    <div className="agenda-row-docs">
                      {e.hydroDocCount > 0 && (
                        <span className="hydro-flag" style={{ fontSize: 10 }}>
                          <span className="hydro-tri"/> {e.hydroDocCount}
                        </span>
                      )}
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        <Icon name="file" size={11}/> {e.documents.length}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

window.AgendaView = AgendaView;
