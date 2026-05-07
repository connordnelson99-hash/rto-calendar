// calendar-view.jsx — month grid with events overlaid

const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const WEEKDAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

const CalendarMonth = ({ year, month, events, selectedDate, onSelectDate, onSelectEvent, today }) => {
  // Build a 6x7 grid starting from the Sunday before the 1st
  const firstOfMonth = new Date(year, month, 1);
  const firstWeekday = firstOfMonth.getDay();
  const start = new Date(year, month, 1 - firstWeekday);
  const days = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    days.push({
      date: d,
      iso,
      isOtherMonth: d.getMonth() !== month,
      isToday: iso === today
    });
  }

  // Group events by date
  const byDate = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      if (!m[e.date]) m[e.date] = [];
      m[e.date].push(e);
    }
    // sort by time within day; hydro-relevant first
    for (const d in m) {
      m[d].sort((a, b) => {
        if (a.isRelevant !== b.isRelevant) return b.isRelevant - a.isRelevant;
        return (a.time || "99:99").localeCompare(b.time || "99:99");
      });
    }
    return m;
  }, [events]);

  return (
    <div className="cal-grid">
      <div className="cal-weekdays">
        {WEEKDAYS.map(w => <div key={w} className="cal-weekday">{w}</div>)}
      </div>
      <div className="cal-days">
        {days.map((d, i) => {
          const dayEvents = byDate[d.iso] || [];
          const visible = dayEvents.slice(0, 3);
          const more = dayEvents.length - visible.length;
          const hydroCount = dayEvents.filter(e => e.isRelevant).length;
          return (
            <div key={i}
                 className={[
                   "cal-day",
                   d.isOtherMonth && "other-month",
                   d.isToday && "today",
                   selectedDate === d.iso && "selected"
                 ].filter(Boolean).join(" ")}
                 onClick={() => onSelectDate(d.iso)}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span className="day-num">{d.date.getDate()}</span>
              </div>
              {visible.map(e => (
                <div key={e.id}
                     className={"cal-event " + (e.isRelevant ? "hydro" : "")}
                     onClick={(ev) => { ev.stopPropagation(); onSelectEvent(e.id); }}
                     style={{ borderLeftColor: e.rtoMeta.color, color: e.rtoMeta.color }}
                     title={`${e.rtoMeta.label}: ${e.title}`}>
                  <span className="ev-time" style={{ color: e.rtoMeta.color, opacity: 0.7 }}>
                    {e.time ? e.time.split(":")[0] + (e.time.endsWith("00") ? "" : ":" + e.time.split(":")[1]) : ""}
                  </span>
                  <span className="ev-title" style={{ color: "var(--text)" }}>
                    {e.rtoMeta.label} · {e.title.replace(/^(PJM|CAISO|MISO|NYISO|ERCOT|SPP|NEPOOL|ISO-NE|NERC|FERC)\s+/, "").replace(/Meeting$/, "").trim()}
                  </span>
                </div>
              ))}
              {more > 0 && (
                <div className="cal-day-more" onClick={(ev) => { ev.stopPropagation(); onSelectDate(d.iso); }}>
                  +{more} more
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const CalendarPane = ({ events, selectedDate, onSelectDate, onSelectEvent, today, monthCursor, setMonthCursor }) => {
  const [y, m] = monthCursor;

  const prev = () => {
    if (m === 0) setMonthCursor([y - 1, 11]); else setMonthCursor([y, m - 1]);
  };
  const next = () => {
    if (m === 11) setMonthCursor([y + 1, 0]); else setMonthCursor([y, m + 1]);
  };
  const goToday = () => {
    const t = new Date(today);
    setMonthCursor([t.getFullYear(), t.getMonth()]);
    onSelectDate(today);
  };

  return (
    <div className="calendar-pane">
      <div className="cal-header">
        <div className="cal-title">{MONTHS[m]} {y}</div>
        <div className="cal-nav">
          <button className="cal-nav-btn" onClick={prev}><Icon name="chevronLeft" size={14}/></button>
          <button className="cal-nav-btn" onClick={next}><Icon name="chevronRight" size={14}/></button>
        </div>
        <button className="cal-today-btn" onClick={goToday}>Today</button>
        <div style={{flex: 1}}/>
        <div className="kbd-hint">
          <kbd>←</kbd><kbd>→</kbd> nav
        </div>
      </div>
      <CalendarMonth year={y} month={m}
        events={events}
        selectedDate={selectedDate}
        onSelectDate={onSelectDate}
        onSelectEvent={onSelectEvent}
        today={today}/>
    </div>
  );
};

window.CalendarPane = CalendarPane;
