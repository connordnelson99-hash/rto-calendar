// search.jsx — command-palette typeahead over the whole corpus.
// As you type, a grouped dropdown surfaces matching stakeholders, organizations,
// committees, initiatives, meetings, topics and markets — each with RTO + type
// tags and a meeting count. Selecting one applies a structured facet to the
// calendar (more precise than free-text), shown as a removable chip in the
// toolbar. Plain text search still works for anyone who ignores the dropdown.

const normName = (s) => String(s || "").trim().toLowerCase();

const FACET_ICON = {
  stakeholder: "users",
  entity:      "inbox",
  committee:   "folder",
  initiative:  "target",
  meeting:     "calendar",
  topic:       "tag",
  rto:         "grid",
};

// ── Index (built once per dataset) ──────────────────────────────
// Aggregates the corpus into per-entity records carrying the RTOs and document
// types each one touches plus a meeting count, so the dropdown can render a
// banner without rescanning on every keystroke.
function buildSearchIndex(data) {
  const events = data.events || [];
  const committees = new Map();   // rto::committee
  const stakeholders = new Map(); // name::entity
  const entities = new Map();     // entity
  const initiatives = new Map();  // rto:native_id

  for (const e of events) {
    if (e.committee) {
      const k = `${e.rto}::${e.committee}`;
      let c = committees.get(k);
      if (!c) committees.set(k, c = {
        kind: "committee", committee: e.committee, rto: e.rto, rtoMeta: e.rtoMeta,
        count: 0, search: normName(e.committee),
      });
      c.count++;
    }

    for (const i of (e.issues || [])) {
      const k = `${i.rto}:${i.native_id}`;
      let it = initiatives.get(k);
      if (!it) initiatives.set(k, it = {
        kind: "initiative", native_id: i.native_id, rto: i.rto,
        rtoMeta: data.rtoMeta[i.rto] || e.rtoMeta,
        name: i.canonical_name || i.native_id, status: i.status, count: 0,
        search: normName(`${i.canonical_name || ""} ${i.native_id || ""}`),
      });
      it.count++;
    }

    // Stakeholders + their organizations. Dedup per event so `count` reads as
    // meetings, not document hits. Skip name==entity artifacts (Haiku
    // occasionally echoes the org as the signatory) — same rule as the
    // detail pane's stakeholder aggregation.
    const seenSH = new Set(), seenEnt = new Set();
    for (const d of (e.documents || [])) {
      const dtype = d.type && !["other", "document"].includes(d.type) ? d.type : null;
      for (const s of (d.stakeholders || [])) {
        const name = (s.name || "").trim();
        if (!name) continue;
        const ent = normalizeEntity(s.entity);
        if (ent && normName(name) === normName(ent)) continue;

        const k = `${normName(name)}::${normName(ent || "")}`;
        let sh = stakeholders.get(k);
        if (!sh) stakeholders.set(k, sh = {
          kind: "stakeholder", name, entity: ent || null,
          rtos: new Set(), types: new Set(), count: 0,
          search: normName(`${name} ${ent || ""}`),
        });
        sh.rtos.add(e.rto);
        if (dtype) sh.types.add(dtype);
        if (!seenSH.has(k)) { sh.count++; seenSH.add(k); }

        if (ent) {
          const ek = normName(ent);
          let en = entities.get(ek);
          if (!en) entities.set(ek, en = {
            kind: "entity", entity: ent, rtos: new Set(), types: new Set(),
            people: new Set(), count: 0, search: ek,
          });
          en.rtos.add(e.rto);
          if (dtype) en.types.add(dtype);
          en.people.add(normName(name));
          if (!seenEnt.has(ek)) { en.count++; seenEnt.add(ek); }
        }
      }
    }
  }

  return {
    committees:   [...committees.values()],
    stakeholders: [...stakeholders.values()],
    entities:     [...entities.values()],
    initiatives:  [...initiatives.values()],
  };
}

// ── Query → grouped suggestions ─────────────────────────────────
function runSearch(index, data, qRaw) {
  const q = normName(qRaw);
  if (q.length < 2) return [];

  // Prefix matches rank above interior matches; ties broken by count.
  const rank = (arr, n = 5) => arr
    .filter(x => x.search.includes(q))
    .sort((a, b) =>
      (b.search.startsWith(q) - a.search.startsWith(q)) || (b.count - a.count))
    .slice(0, n);

  const rtos = Object.entries(data.rtoMeta)
    .filter(([k, m]) => normName(m.label).includes(q) || normName(k).includes(q))
    .map(([k, m]) => ({
      kind: "rto", rto: k, rtoMeta: m, label: m.label,
      count: data.events.filter(e => e.rto === k).length,
    }))
    .filter(x => x.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);

  const topics = Object.entries(data.topicMeta || {})
    .filter(([k, m]) => normName(m.label).includes(q) || k.includes(q))
    .map(([k, m]) => ({
      kind: "topic", topic: k, label: m.label,
      count: data.events.filter(e => (e.topics || []).includes(k)).length,
    }))
    .filter(x => x.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);

  const meetings = data.events
    .filter(e => normName(e.title).includes(q))
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 5)
    .map(e => ({
      kind: "meeting", id: e.id, title: e.title, rto: e.rto, rtoMeta: e.rtoMeta,
      committee: e.committee, date: e.date,
    }));

  const out = [];
  const push = (label, items) => { if (items.length) out.push({ label, items }); };
  push("Stakeholders",  rank(index.stakeholders));
  push("Organizations", rank(index.entities));
  push("Committees",    rank(index.committees));
  push("Initiatives",   rank(index.initiatives));
  push("Meetings",      meetings);
  push("Topics",        topics);
  push("Markets",       rtos);
  return out;
}

// ── Facet predicate (shared by the calendar filter) ─────────────
function matchesFacet(e, f) {
  if (!f) return true;
  switch (f.kind) {
    case "committee":
      return e.committee === f.committee && e.rto === f.rto;
    case "stakeholder":
      return (e.documents || []).some(d => (d.stakeholders || []).some(s =>
        normName(s.name) === f.name &&
        (normalizeEntity(s.entity) || null) === (f.entity || null)));
    case "entity":
      return (e.documents || []).some(d => (d.stakeholders || []).some(s =>
        normalizeEntity(s.entity) === f.entity));
    case "initiative":
      return (e.issues || []).some(i => i.rto === f.rto && i.native_id === f.native_id);
    default:
      return true;
  }
}

// ── Suggestion row ──────────────────────────────────────────────
const RtoChips = ({ rtos, data, max = 4 }) => {
  const list = [...rtos];
  return (
    <>
      {list.slice(0, max).map(r => (
        <RtoTag key={r} rto={r} meta={data.rtoMeta[r] || data.rtoMeta.Other}/>
      ))}
      {list.length > max && <span className="search-more">+{list.length - max}</span>}
    </>
  );
};

const TypeChips = ({ types, max = 3 }) => {
  const list = [...types];
  if (!list.length) return null;
  return (
    <>
      {list.slice(0, max).map(t => (
        <span key={t} className="search-type-chip">{docTypeLabel(t)}</span>
      ))}
      {list.length > max && <span className="search-more">+{list.length - max}</span>}
    </>
  );
};

const SuggestionRow = ({ item, active, data, onClick, onMouseEnter }) => {
  let title, subtitle = null, tags = null, count = item.count;

  switch (item.kind) {
    case "stakeholder":
      title = item.name;
      subtitle = item.entity;
      tags = <><RtoChips rtos={item.rtos} data={data}/><TypeChips types={item.types}/></>;
      break;
    case "entity":
      title = item.entity;
      subtitle = item.people.size === 1 ? "1 person" : `${item.people.size} people`;
      tags = <><RtoChips rtos={item.rtos} data={data}/><TypeChips types={item.types}/></>;
      break;
    case "committee":
      title = item.committee;
      tags = <RtoTag rto={item.rto} meta={item.rtoMeta}/>;
      break;
    case "initiative":
      title = item.name;
      tags = <>
        <RtoTag rto={item.rto} meta={item.rtoMeta}/>
        {item.status && <span className="search-type-chip">{item.status}</span>}
      </>;
      break;
    case "meeting":
      title = item.title;
      subtitle = item.committee;
      tags = <>
        <RtoTag rto={item.rto} meta={item.rtoMeta}/>
        <span className="search-date">
          {new Date(item.date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        </span>
      </>;
      count = null;
      break;
    case "topic":
      title = item.label;
      tags = <span className="search-type-chip">topic</span>;
      break;
    case "rto":
      title = item.label;
      tags = <RtoTag rto={item.rto} meta={item.rtoMeta}/>;
      break;
  }

  return (
    <div className={"search-item" + (active ? " active" : "")}
         onMouseEnter={onMouseEnter}
         onMouseDown={(e) => { e.preventDefault(); onClick(); }}>
      <div className="search-item-icon"><Icon name={FACET_ICON[item.kind]} size={14}/></div>
      <div className="search-item-body">
        <div className="search-item-title">
          {title}
          {subtitle && <span className="search-item-sub">{subtitle}</span>}
        </div>
        {tags && <div className="search-item-tags">{tags}</div>}
      </div>
      {count != null && (
        <div className="search-item-count" title={`${count} meeting${count === 1 ? "" : "s"}`}>{count}</div>
      )}
    </div>
  );
};

// ── Search box ──────────────────────────────────────────────────
function SearchBox({ data, filters, setFilters, onSelectEvent }) {
  const index = React.useMemo(() => buildSearchIndex(data), [data]);
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const wrapRef = React.useRef(null);
  const inputRef = React.useRef(null);

  const groups = React.useMemo(
    () => (open ? runSearch(index, data, filters.q) : []),
    [open, index, data, filters.q]
  );
  const flat = React.useMemo(() => groups.flatMap(g => g.items), [groups]);

  // Reset highlight to the top match whenever the result set changes.
  React.useEffect(() => { setActive(0); }, [filters.q]);

  // Close on outside click; ⌘K / Ctrl-K focuses the box from anywhere.
  React.useEffect(() => {
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current && inputRef.current.focus();
        if (filters.q) setOpen(true);
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [filters.q]);

  const applySuggestion = (item) => {
    if (!item) return;
    switch (item.kind) {
      case "rto":
        setFilters({ ...filters, q: "", facet: null, rto: item.rto });
        break;
      case "topic":
        setFilters({ ...filters, q: "", facet: null, topic: item.topic });
        break;
      case "meeting":
        setFilters({ ...filters, q: "" });
        onSelectEvent(item.id);
        break;
      case "committee":
        setFilters({ ...filters, q: "", facet: {
          kind: "committee", committee: item.committee, rto: item.rto, label: item.committee,
        }});
        break;
      case "stakeholder":
        setFilters({ ...filters, q: "", facet: {
          kind: "stakeholder", name: normName(item.name), entity: item.entity || null,
          label: item.entity ? `${item.name} · ${item.entity}` : item.name,
        }});
        break;
      case "entity":
        setFilters({ ...filters, q: "", facet: {
          kind: "entity", entity: item.entity, label: item.entity,
        }});
        break;
      case "initiative":
        setFilters({ ...filters, q: "", facet: {
          kind: "initiative", rto: item.rto, native_id: item.native_id, label: item.name,
        }});
        break;
    }
    setOpen(false);
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setActive(a => Math.min(a + 1, flat.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(a => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      if (open && flat[active]) { e.preventDefault(); applySuggestion(flat[active]); }
      else setOpen(false);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="topbar-search-wrap" ref={wrapRef}>
      <div className="topbar-search">
        <Icon name="search" size={14} className="search-icon"/>
        <input
          ref={inputRef}
          placeholder="Search meetings, committees, initiatives, stakeholders…"
          value={filters.q}
          onChange={e => { setFilters({ ...filters, q: e.target.value, facet: null }); setOpen(true); }}
          onFocus={() => { if (filters.q) setOpen(true); }}
          onKeyDown={onKeyDown}/>
        <span className="kbd">⌘K</span>
      </div>
      {open && groups.length > 0 && (
        <div className="search-dropdown">
          {groups.map(g => (
            <div key={g.label} className="search-group">
              <div className="search-group-label">{g.label}</div>
              {g.items.map(item => (
                <SuggestionRow
                  key={`${item.kind}:${item.id || item.native_id || item.search || item.label}`}
                  item={item}
                  data={data}
                  active={flat[active] === item}
                  onMouseEnter={() => setActive(flat.indexOf(item))}
                  onClick={() => applySuggestion(item)}/>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

window.SearchBox = SearchBox;
window.matchesFacet = matchesFacet;
window.FACET_ICON = FACET_ICON;
