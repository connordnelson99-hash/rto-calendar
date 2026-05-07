// data.js — fetches live rto_events_with_docs.json and shapes it into MARKETS_DATA.
// No synthetic doc summaries: real ai_summary / hydro_relevance_reason / hydro_relevant
// from the screening pipeline pass through unchanged. RTO scope is open — every RTO
// in the feed gets rendered (no ACTIVE_RTOS gate).

(function () {
  const RTO_META = {
    PJM:      { color: "#3B82F6", bg: "#EFF6FF", label: "PJM" },
    CAISO:    { color: "#F59E0B", bg: "#FFFBEB", label: "CAISO" },
    MISO:     { color: "#10B981", bg: "#ECFDF5", label: "MISO" },
    NYISO:    { color: "#EF4444", bg: "#FEF2F2", label: "NYISO" },
    ERCOT:    { color: "#A855F7", bg: "#FAF5FF", label: "ERCOT" },
    "ISO-NE": { color: "#06B6D4", bg: "#ECFEFF", label: "ISO-NE" },
    "SPP Markets +": { color: "#D97706", bg: "#FEF3C7", label: "SPP" },
    SPP:      { color: "#D97706", bg: "#FEF3C7", label: "SPP" },
    NEPOOL:   { color: "#0891B2", bg: "#ECFEFF", label: "NEPOOL" },
    NERC:     { color: "#EC4899", bg: "#FDF2F8", label: "NERC" },
    FERC:     { color: "#64748B", bg: "#F1F5F9", label: "FERC" },
    Other:    { color: "#94A3B8", bg: "#F8FAFC", label: "Other" }
  };

  function todayIso() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function addDaysIso(iso, n) {
    const d = new Date(iso + "T12:00:00");
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function formatDateLong(iso) {
    if (!iso) return null;
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  }

  // Live JSON gives many formats:
  //   ISO-NE: "1:30 PM"
  //   CAISO:  "9:00 AM - 12:00 PM"
  //   PJM:    "1:00 p.m. - 4:00 p.m."
  //   FERC:   null
  // Calendar/week views need a "HH:MM" 24h start time. We parse the leading
  // time of a range and accept both "PM" and "p.m." am/pm markers.
  function parseTimeTo24(t) {
    if (!t) return null;
    const s = String(t).trim();
    if (!s) return null;
    const startStr = s.split(/\s*[-–]\s*/)[0].trim();
    const m = startStr.match(/^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$/i);
    if (!m) return null;
    let h = parseInt(m[1], 10);
    const min = m[2] || "00";
    const ap = m[3].toLowerCase();
    if (ap === "p" && h !== 12) h += 12;
    if (ap === "a" && h === 12) h = 0;
    return `${String(h).padStart(2, "0")}:${min}`;
  }

  // Take raw {rto, native_id, ...} stub from a doc's `issues` array and
  // merge with the full record from rto_issues.json (which has timeline
  // dates, etc.). If we don't have the full record, fall back to the stub.
  function resolveIssue(stub, byKey) {
    const key = `${stub.rto}:${stub.native_id}`;
    return byKey.get(key) || stub;
  }

  function buildEvent(raw, idx, issuesByKey) {
    const rto = raw.rto || "Other";
    const rtoMeta = RTO_META[rto] || RTO_META.Other;

    const documents = (raw.documents || []).map((d, i) => {
      const issues = (d.issues || []).map(s => resolveIssue(s, issuesByKey));
      return {
        id: `${idx}-${i}`,
        type: d.type || "document",
        title: d.title,
        filename: d.filename,
        url: d.url,
        posted_date: d.posted_date,
        hydro_relevant: d.hydro_relevant === true,
        hydro_relevance_reason: d.hydro_relevance_reason || null,
        ai_summary: d.ai_summary || null,
        issues,
        stakeholders: d.stakeholders || [],
      };
    });

    // Deduplicate issues across all sources (meeting-level + doc-level) by
    // native_id. PJM cites individual document URLs so its issues come in
    // through `documents[].issues`; CAISO cites per-meeting calendar URLs
    // so its issues come in through `raw.issues` on the meeting itself.
    const seenIssues = new Map();
    for (const stub of (raw.issues || [])) {
      const key = `${stub.rto}:${stub.native_id}`;
      if (!seenIssues.has(key)) seenIssues.set(key, resolveIssue(stub, issuesByKey));
    }
    for (const d of documents) {
      for (const iss of d.issues) {
        const key = `${iss.rto}:${iss.native_id}`;
        if (!seenIssues.has(key)) seenIssues.set(key, iss);
      }
    }
    const issues = [...seenIssues.values()];

    const meetingHydro = raw.meeting_hydro_relevant === true;
    const hasHydroDocs = documents.some(d => d.hydro_relevant);
    const time24 = parseTimeTo24(raw.time);

    return {
      id: String(idx),
      title: raw.title,
      date: raw.date,
      dateRaw: formatDateLong(raw.date),
      time: time24,
      timeFmt: raw.time ? `${raw.time} ET` : null,
      timeRaw: raw.time,
      rto,
      rtoMeta,
      committee: raw.committee || null,
      sourceUrl: raw.source_url || null,
      detailUrl: raw.detail_url || null,
      materialsUrl: raw.materials_url || null,
      meetingHydroRelevant: meetingHydro,
      meetingHydroReason: raw.meeting_hydro_reason || null,
      documents,
      hydroDocCount: documents.filter(d => d.hydro_relevant).length,
      isRelevant: meetingHydro || hasHydroDocs,
      issues,
      hasIssues: issues.length > 0,
    };
  }

  function buildMarketsData(rawEvents, rawIssues) {
    const issuesByKey = new Map();
    for (const i of (rawIssues || [])) {
      issuesByKey.set(`${i.rto}:${i.native_id}`, i);
    }

    const events = rawEvents
      .map((e, idx) => buildEvent(e, idx, issuesByKey))
      .filter(e => e.date);

    const today = todayIso();
    const weekEnd = addDaysIso(today, 6);

    // Morning digest: hydro-relevant items in the next 7 days
    const digestItems = events
      .filter(e => e.isRelevant && e.date >= today && e.date <= weekEnd)
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(0, 6);

    return {
      events,
      rtoMeta: RTO_META,
      today,
      weekStart: today,
      weekEnd,
      digestItems,
    };
  }

  window.RTO_META = RTO_META;

  window.loadMarketsData = async function () {
    // Data files live at <repo-root>/rto-docs/ — one level up from
    // webcal-v2/. Cache-bust each load so stale GitHub Pages caches don't
    // hide freshly-scraped JSON.
    const t = Date.now();
    const eventsUrl = `../rto-docs/rto_events_with_docs.json?t=${t}`;
    const issuesUrl = `../rto-docs/rto_issues.json?t=${t}`;

    const [eventsRes, issuesRes] = await Promise.all([
      fetch(eventsUrl),
      fetch(issuesUrl).catch(() => null), // optional: older deployments may lack this file
    ]);
    if (!eventsRes.ok) throw new Error(`Failed to load events: ${eventsRes.status} ${eventsRes.statusText}`);
    const rawEvents = await eventsRes.json();
    const rawIssues = (issuesRes && issuesRes.ok) ? await issuesRes.json() : [];

    window.MARKETS_DATA = buildMarketsData(rawEvents, rawIssues);
    return window.MARKETS_DATA;
  };
})();
