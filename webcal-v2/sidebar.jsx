// sidebar.jsx — left navigation: nav items + RTO list (sorted by count).

const Sidebar = ({ filters, setFilters, events, today, weekEnd, digestCount }) => {
  const rtoCounts = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      m[e.rto] = (m[e.rto] || 0) + 1;
    }
    return m;
  }, [events]);

  // Order RTOs by event count (descending), no hardcoded scope filter.
  const rtoOrder = React.useMemo(() => {
    return Object.keys(rtoCounts).sort((a, b) => rtoCounts[b] - rtoCounts[a]);
  }, [rtoCounts]);

  const rtoMeta = window.MARKETS_DATA.rtoMeta;

  const thisWeekCount = React.useMemo(
    () => events.filter(e => e.date >= today && e.date <= weekEnd).length,
    [events, today, weekEnd]
  );

  const navItems = [
    { id: "all", label: "All meetings", icon: "calendar", count: events.length },
    { id: "hydro", label: "Hydro-relevant", icon: "drop", count: events.filter(e => e.isRelevant).length },
    { id: "today", label: "This week", icon: "clock", count: thisWeekCount },
    { id: "digest", label: "Morning digest", icon: "inbox", count: digestCount },
  ];

  const setRto = (rto) => setFilters({ ...filters, rto: filters.rto === rto ? "all" : rto });

  return (
    <aside className="sidebar">
      <div className="sidebar-section">
        <div className="sidebar-label">Views</div>
        {navItems.map(item => (
          <div key={item.id}
               className={"nav-item " + (filters.view === item.id ? "active" : "")}
               onClick={() => setFilters({ ...filters, view: item.id })}>
            <span className="nav-icon">
              <Icon name={item.icon} size={14}/>
            </span>
            <span>{item.label}</span>
            <span className="nav-count">{item.count}</span>
          </div>
        ))}
      </div>

      <div className="sidebar-section">
        <div className="sidebar-label">RTOs / ISOs</div>
        {rtoOrder.map(rto => {
          const meta = rtoMeta[rto] || rtoMeta.Other;
          const active = filters.rto === rto;
          return (
            <div key={rto}
                 className={"nav-item " + (active ? "active" : "")}
                 onClick={() => setRto(rto)}>
              <span className="rto-dot" style={{ background: meta.color }}/>
              <span>{meta.label}</span>
              <span className="nav-count">{rtoCounts[rto]}</span>
            </div>
          );
        })}
      </div>

      <div style={{flex: 1}}/>
    </aside>
  );
};

window.Sidebar = Sidebar;
