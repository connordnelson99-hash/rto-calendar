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

  // ISO Monday of the week containing iso (yyyy-mm-dd in).
  function isoWeekStart(iso) {
    const d = new Date(iso + "T12:00:00");
    const dow = d.getDay(); // 0=Sun..6=Sat
    const shift = dow === 0 ? 6 : dow - 1;
    d.setDate(d.getDate() - shift);
    return d.toISOString().slice(0, 10);
  }

  // Each RTO publishes meeting times in its own local zone (CAISO=PT,
  // PJM/NYISO/ISO-NE/FERC=ET, MISO/SPP/ERCOT=CT). The scraped `time`
  // string carries no zone, so we tag the source zone here and convert
  // to the viewer's local zone at render time.
  const RTO_SOURCE_TZ = {
    PJM:             "America/New_York",
    NYISO:           "America/New_York",
    "ISO-NE":        "America/New_York",
    NEPOOL:          "America/New_York",
    FERC:            "America/New_York",
    NERC:            "America/New_York",
    CAISO:           "America/Los_Angeles",
    MISO:            "America/Chicago",
    SPP:             "America/Chicago",
    "SPP Markets +": "America/Chicago",
    ERCOT:           "America/Chicago",
    // Other: unknown — leave display as scraped.
  };

  const USER_TZ = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone; }
    catch (_) { return "America/New_York"; }
  })();

  // Parse "9:00 AM - 12:00 PM" / "1:00 p.m. - 4:00 p.m." / "4:00 PM" / null.
  // Returns { startH, startM, endH?, endM? } or null.
  function parseTimeRange(t) {
    if (!t) return null;
    const s = String(t).trim();
    if (!s) return null;
    const halves = s.split(/\s*[-–]\s*/);
    const one = (str) => {
      const m = str.trim().match(/^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$/i);
      if (!m) return null;
      let h = parseInt(m[1], 10);
      const min = parseInt(m[2] || "0", 10);
      const ap = m[3].toLowerCase();
      if (ap === "p" && h !== 12) h += 12;
      if (ap === "a" && h === 12) h = 0;
      return { h, m: min };
    };
    const a = one(halves[0]);
    if (!a) return null;
    const b = halves.length > 1 ? one(halves[1]) : null;
    return { startH: a.h, startM: a.m, endH: b ? b.h : null, endM: b ? b.m : null };
  }

  // Wall-clock (yyyy-mm-dd, hh, mm) in `tz` → UTC Date. DST-correct: we
  // construct a "naive UTC" instant for the wall time, ask Intl what zone
  // sees that instant as, and subtract the resulting offset.
  function wallToUtc(dateIso, hh, mm, tz) {
    const naive = Date.UTC(
      +dateIso.slice(0, 4), +dateIso.slice(5, 7) - 1, +dateIso.slice(8, 10),
      hh, mm, 0
    );
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(new Date(naive));
    const o = Object.fromEntries(parts.filter(p => p.type !== "literal").map(p => [p.type, p.value]));
    const tzAsIfUtc = Date.UTC(
      +o.year, +o.month - 1, +o.day,
      (+o.hour === 24 ? 0 : +o.hour), +o.minute, +o.second
    );
    const offsetMs = tzAsIfUtc - naive;
    return new Date(naive - offsetMs);
  }

  function fmtClock12(date, tz) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hour: "numeric", minute: "2-digit", hour12: true,
    }).format(date);
  }
  function fmtClock24(date, tz) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(date);
    const o = Object.fromEntries(parts.filter(p => p.type !== "literal").map(p => [p.type, p.value]));
    const h = +o.hour === 24 ? "00" : o.hour;
    return `${h}:${o.minute}`;
  }
  function fmtZoneShort(date, tz) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, timeZoneName: "short",
    }).formatToParts(date);
    return parts.find(p => p.type === "timeZoneName")?.value || "";
  }
  function fmtRange(startDate, endDate, tz) {
    const z = fmtZoneShort(startDate, tz);
    if (!endDate) return `${fmtClock12(startDate, tz)} ${z}`.trim();
    return `${fmtClock12(startDate, tz)} – ${fmtClock12(endDate, tz)} ${z}`.trim();
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

    // Resolve display time in the viewer's zone. If we know the RTO's
    // source zone we convert; otherwise (Other) we fall back to the raw
    // string so the viewer still sees something sensible.
    const sourceTz = RTO_SOURCE_TZ[rto] || null;
    const parsed = parseTimeRange(raw.time);
    let time24 = null;
    let timeFmt = null;
    let timeZoneShort = null;
    let sourceTimeFmt = null;
    if (parsed && sourceTz && raw.date) {
      const utcStart = wallToUtc(raw.date, parsed.startH, parsed.startM, sourceTz);
      const utcEnd = parsed.endH != null
        ? wallToUtc(raw.date, parsed.endH, parsed.endM, sourceTz)
        : null;
      time24 = fmtClock24(utcStart, USER_TZ);
      timeFmt = fmtRange(utcStart, utcEnd, USER_TZ);
      timeZoneShort = fmtZoneShort(utcStart, USER_TZ);
      if (sourceTz !== USER_TZ) {
        sourceTimeFmt = fmtRange(utcStart, utcEnd, sourceTz);
      }
    } else if (parsed) {
      const hh = String(parsed.startH).padStart(2, "0");
      const mm = String(parsed.startM).padStart(2, "0");
      time24 = `${hh}:${mm}`;
      timeFmt = raw.time;
    }

    return {
      id: String(idx),
      title: raw.title,
      date: raw.date,
      dateRaw: formatDateLong(raw.date),
      time: time24,
      timeFmt,
      timeZoneShort,
      sourceTimeFmt,
      sourceTz,
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
    const currentWeekStart = isoWeekStart(today);

    // Weekly digest: one bucket per ISO week (Mon–Sun) that the dataset
    // touches, plus the current week even if empty. Past weeks pull richer
    // signal (`isRelevant = meetingHydro || hasHydroDocs`) because scraped
    // documents have arrived; the current week is mostly title-driven.
    const eventDates = events.map(e => e.date).filter(Boolean);
    const minDate = eventDates.length
      ? eventDates.reduce((a, b) => (a < b ? a : b))
      : today;
    const maxDate = eventDates.length
      ? eventDates.reduce((a, b) => (a > b ? a : b))
      : today;
    const firstWeekStart = isoWeekStart(minDate);
    const lastWeekStart = isoWeekStart(maxDate > today ? maxDate : today);

    const weeks = [];
    for (let ws = firstWeekStart; ws <= lastWeekStart; ws = addDaysIso(ws, 7)) {
      const we = addDaysIso(ws, 6);
      const items = events
        .filter(e => e.isRelevant && e.date >= ws && e.date <= we)
        .sort((a, b) => a.date.localeCompare(b.date));
      weeks.push({ key: ws, weekStart: ws, weekEnd: we, items });
    }

    const currentWeek =
      weeks.find(w => w.key === currentWeekStart) || {
        key: currentWeekStart,
        weekStart: currentWeekStart,
        weekEnd: addDaysIso(currentWeekStart, 6),
        items: [],
      };

    return {
      events,
      rtoMeta: RTO_META,
      today,
      weekStart: currentWeek.weekStart,
      weekEnd: currentWeek.weekEnd,
      // Backwards-compat shortcut for the list-view banner and badge,
      // which want a glance at the current ISO week.
      digestItems: currentWeek.items,
      weeks,
      currentWeekKey: currentWeekStart,
    };
  }

  // Markdown export of the weekly digest. Same hydro filter as the popup,
  // but uncapped and with full ai_summary/stakeholder/issue detail.
  // Designed to be pasted into a desktop Claude session that already knows
  // how to turn it into an email digest. `weekKey` selects which ISO week
  // to serialize; defaults to the current week.
  function buildDigestMarkdown(data, weekKey) {
    const weeks = (data && data.weeks) || [];
    const key = weekKey || (data && data.currentWeekKey);
    const wk =
      weeks.find(w => w.key === key) || {
        weekStart: data && data.weekStart,
        weekEnd: data && data.weekEnd,
        items: (data && data.digestItems) || [],
      };
    const items = wk.items;
    const today = data && data.today;
    const weekStart = wk.weekStart;
    const weekEnd = wk.weekEnd;

    const todayLong = formatDateLong(today);
    const weekStartLong = formatDateLong(weekStart);
    const weekEndLong = formatDateLong(weekEnd);

    const out = [];
    out.push(`# RTO Hydro Digest — Week of ${weekStartLong}`);
    out.push("");
    out.push(`_Generated ${todayLong}. Window: ${weekStartLong} – ${weekEndLong}._`);
    out.push("");

    if (items.length === 0) {
      out.push("No hydro-relevant meetings flagged in this window.");
      return out.join("\n");
    }

    const totalDocs = items.reduce((s, e) => s + e.documents.length, 0);
    const totalHydroDocs = items.reduce((s, e) => s + e.hydroDocCount, 0);
    const rtos = Array.from(new Set(items.map(e => e.rtoMeta.label))).sort();

    out.push(
      `**${items.length} hydro-relevant meeting${items.length === 1 ? "" : "s"}** ` +
      `across ${rtos.join(", ")}. ` +
      `${totalHydroDocs} hydro-flagged document${totalHydroDocs === 1 ? "" : "s"} ` +
      `of ${totalDocs} total.`
    );
    out.push("");

    items.forEach((e, i) => {
      out.push("---");
      out.push("");
      out.push(`## ${i + 1}. ${e.rtoMeta.label} — ${e.title}`);
      out.push("");
      const dateLine = e.timeFmt ? `${e.dateRaw} · ${e.timeFmt}` : e.dateRaw;
      out.push(`- **Date:** ${dateLine}`);
      if (e.committee) out.push(`- **Committee:** ${e.committee}`);
      if (e.sourceUrl) out.push(`- **Source:** ${e.sourceUrl}`);
      if (e.detailUrl) out.push(`- **Detail page:** ${e.detailUrl}`);
      if (e.materialsUrl) out.push(`- **Materials:** ${e.materialsUrl}`);
      out.push("");

      if (e.meetingHydroReason) {
        out.push(`**Why hydro-relevant:** ${e.meetingHydroReason}`);
        out.push("");
      }

      if (e.issues && e.issues.length) {
        out.push(`### Initiatives (${e.issues.length})`);
        out.push("");
        for (const iss of e.issues) {
          const name = iss.canonical_name || iss.title || iss.name || iss.short_title || iss.native_id || "(unnamed)";
          const tag = iss.short_title && iss.short_title !== name ? ` (${iss.short_title})` : "";
          const status = iss.status ? ` — _${iss.status}_` : "";
          out.push(`- **${name}**${tag}${status}`);
          if (iss.stakeholder_phase) out.push(`  - Stakeholder phase: ${iss.stakeholder_phase}`);
          if (iss.committee_owner_label) out.push(`  - Owner: ${iss.committee_owner_label}`);
          if (iss.url) out.push(`  - ${iss.url}`);
        }
        out.push("");
      }

      const docs = (e.documents || []).slice().sort(
        (a, b) => Number(b.hydro_relevant) - Number(a.hydro_relevant)
      );
      const docHeader = e.hydroDocCount > 0
        ? `${docs.length} total, ${e.hydroDocCount} hydro-relevant`
        : `${docs.length} total`;
      out.push(`### Documents (${docHeader})`);
      out.push("");

      if (docs.length === 0) {
        out.push("_No documents attached._");
        out.push("");
      } else {
        for (const d of docs) {
          const tag = d.hydro_relevant ? "[HYDRO] " : "";
          out.push(`#### ${tag}${d.title || d.filename || "(untitled)"}`);
          const meta = [];
          if (d.type) meta.push(`**Type:** ${d.type}`);
          if (d.posted_date) meta.push(`**Posted:** ${d.posted_date}`);
          if (meta.length) out.push(`- ${meta.join(" · ")}`);
          if (d.hydro_relevant && d.hydro_relevance_reason) {
            out.push(`- **Why flagged:** ${d.hydro_relevance_reason}`);
          }
          if (d.stakeholders && d.stakeholders.length) {
            const names = d.stakeholders
              .map(s => (s.entity ? `${s.name} (${s.entity})` : s.name))
              .filter(Boolean)
              .join("; ");
            if (names) out.push(`- **Stakeholders:** ${names}`);
          }
          if (d.issues && d.issues.length) {
            const iNames = d.issues
              .map(i => i.short_title || i.canonical_name || i.native_id)
              .filter(Boolean)
              .join("; ");
            if (iNames) out.push(`- **Initiatives touched:** ${iNames}`);
          }
          if (d.url) out.push(`- **URL:** ${d.url}`);
          out.push("");
          if (d.ai_summary) {
            out.push("**AI summary:**");
            out.push("");
            out.push(d.ai_summary);
            out.push("");
          }
        }
      }
    });

    return out.join("\n");
  }

  window.buildDigestMarkdown = buildDigestMarkdown;

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
