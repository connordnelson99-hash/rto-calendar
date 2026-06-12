// sidebar.jsx — left navigation: nav items + RTO list (sorted by count).

const Sidebar = ({ filters, setFilters, events, today, weekEnd, digestCount, theme, onToggleTheme }) => {
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
  const topicMeta = window.MARKETS_DATA.topicMeta || {};

  // Topic counts across all events (an event carries the union of its
  // docs' tags). Only topics that actually occur are listed; the whole
  // section hides on older exports that pre-date tagging.
  const topicCounts = React.useMemo(() => {
    const m = {};
    for (const e of events) {
      for (const t of (e.topics || [])) m[t] = (m[t] || 0) + 1;
    }
    return m;
  }, [events]);
  const topicOrder = React.useMemo(
    () => Object.keys(topicCounts).sort((a, b) => topicCounts[b] - topicCounts[a]),
    [topicCounts]);

  const setTopic = (topic) =>
    setFilters({ ...filters, topic: filters.topic === topic ? "all" : topic });

  const navItems = [
    { id: "all", label: "All meetings", icon: "calendar", count: events.length },
    { id: "hydro", label: "Hydro-relevant", icon: "drop", count: events.filter(e => e.isRelevant).length },
    { id: "initiative", label: "Initiative-linked", icon: "target", count: events.filter(e => e.hasIssues).length },
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

      {topicOrder.length > 0 && (
        <div className="sidebar-section">
          <div className="sidebar-label">Topics</div>
          {topicOrder.map(topic => {
            const active = filters.topic === topic;
            return (
              <div key={topic}
                   className={"nav-item " + (active ? "active" : "")}
                   onClick={() => setTopic(topic)}>
                <span className="nav-icon">
                  <Icon name="tag" size={12}/>
                </span>
                <span>{topicMeta[topic]?.label || topic}</span>
                <span className="nav-count">{topicCounts[topic]}</span>
              </div>
            );
          })}
        </div>
      )}

      <div style={{flex: 1}}/>

      <div className="sidebar-footer">
        <button className="theme-toggle" onClick={onToggleTheme}
                title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}>
          <Icon name={theme === "dark" ? "sun" : "moon"} size={14}/>
          <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
        </button>
      </div>
    </aside>
  );
};

window.Sidebar = Sidebar;
