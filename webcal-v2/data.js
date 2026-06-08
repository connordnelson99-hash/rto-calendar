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

  // ── "Export data" — zip the full hydro corpus for analysis in Claude ──────
  // No external zip library (the app pins React/Babel with SRI and we don't
  // want an unpinned CDN dependency), so we write a minimal store-only ZIP.

  function _crc32(bytes) {
    let table = _crc32.table;
    if (!table) {
      table = _crc32.table = new Uint32Array(256);
      for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
        table[n] = c >>> 0;
      }
    }
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) crc = (crc >>> 8) ^ table[(crc ^ bytes[i]) & 0xFF];
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  const _u16 = (v) => new Uint8Array([v & 0xff, (v >>> 8) & 0xff]);
  const _u32 = (v) => new Uint8Array([v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]);
  function _concat(parts) {
    let len = 0;
    for (const p of parts) len += p.length;
    const out = new Uint8Array(len);
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    return out;
  }

  // files: [{ name, content }] (content = string). Returns a Blob (store-only).
  function buildZipBlob(files) {
    const enc = new TextEncoder();
    const DOS_TIME = 0;
    const DOS_DATE = ((2026 - 1980) << 9) | (6 << 5) | 5;  // fixed 2026-06-05
    const out = [];
    const central = [];
    let offset = 0;

    for (const f of files) {
      const nameBytes = enc.encode(f.name);
      const dataBytes = enc.encode(f.content);
      const crc = _crc32(dataBytes);
      const local = _concat([
        _u32(0x04034b50), _u16(20), _u16(0x0800), _u16(0),
        _u16(DOS_TIME), _u16(DOS_DATE),
        _u32(crc), _u32(dataBytes.length), _u32(dataBytes.length),
        _u16(nameBytes.length), _u16(0), nameBytes, dataBytes,
      ]);
      out.push(local);
      central.push(_concat([
        _u32(0x02014b50), _u16(20), _u16(20), _u16(0x0800), _u16(0),
        _u16(DOS_TIME), _u16(DOS_DATE),
        _u32(crc), _u32(dataBytes.length), _u32(dataBytes.length),
        _u16(nameBytes.length), _u16(0), _u16(0), _u16(0), _u16(0),
        _u32(0), _u32(offset), nameBytes,
      ]));
      offset += local.length;
    }

    const centralStart = offset;
    let centralSize = 0;
    for (const c of central) { out.push(c); centralSize += c.length; }
    out.push(_concat([
      _u32(0x06054b50), _u16(0), _u16(0),
      _u16(central.length), _u16(central.length),
      _u32(centralSize), _u32(centralStart), _u16(0),
    ]));
    return new Blob(out, { type: "application/zip" });
  }

  function buildCorpusReadme(meta) {
    const breakdown = Object.entries(meta.byRto || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${k} (${v})`)
      .join(", ");
    return `# NHA RTO/ISO Markets Calendar — Hydro Document Corpus

This archive is an export from the National Hydropower Association (NHA) RTO/ISO
Markets Calendar — a member tool that tracks regulatory meetings and documents
across the U.S. wholesale electricity markets (PJM, CAISO, ISO-NE, NYISO, FERC)
and uses Claude to flag content relevant to the hydropower and pumped-storage
industry.

Exported ${meta.dateStr}. Contains ${meta.total} hydro-flagged documents${breakdown ? ` across: ${breakdown}` : ""}.
${meta.filterNote ? meta.filterNote + "\n" : ""}Every record has already been screened as hydro-relevant by the calendar's
pipeline — this is the filtered corpus, not the raw firehose.

## What's in this archive

- \`rto_hydro_corpus.json\` — every hydro-relevant document, as full structured records.
- \`rto_hydro_corpus.csv\`  — the same data as a flat table (one row per document)
  for spreadsheet pivots and quick counts.
- \`CLAUDE.md\` — this file.

## Purpose

The calendar's weekly digest answers "what's happening this week." This export
answers the broader, cross-cutting questions — themes that span many RTOs and
many months. For example: "How many different RTOs/ISOs are currently running
storage-as-transmission-asset discussions?" or "Which markets are advancing
capacity accreditation relevant to hydro, and who are the stakeholders driving
it?" Because every record carries an AI summary, you (Claude) can answer
thematic questions directly by reading across the corpus — you are not limited
to a fixed set of predefined topics.

## Field dictionary

Each JSON record / CSV row has:

- \`rto\` — market / system operator (PJM, CAISO, ISO-NE, NYISO, FERC).
- \`meeting_date\` — date of the meeting the document belongs to (YYYY-MM-DD).
- \`committee\` — committee or working group.
- \`meeting_title\` — title of the meeting.
- \`doc_type\` — agenda, minutes, presentation, report, vote, manual, etc.
- \`title\` — document title.
- \`posted_date\` — when the document was posted, if known.
- \`relevance_reason\` — why the screener flagged it as hydro-relevant.
- \`initiatives\` — linked market initiatives/issues (with status), where available.
- \`stakeholders\` — named authors/contacts and their organizations, where extracted.
- \`ai_summary\` — an AI-generated summary of the document's contents.
- \`url\` — direct link to the ORIGINAL source document on the RTO/ISO website.
  Fetch this to go beyond the summary (see "Pulling original source material").

In the CSV, the \`initiatives\` and \`stakeholders\` lists are joined with "; ".

## How to use this

1. Upload \`rto_hydro_corpus.json\` (richer) or \`rto_hydro_corpus.csv\` (lighter)
   to a Claude conversation along with this file.
2. Ask a cross-cutting question. Good starting prompts:
   - "Across all RTOs, which are discussing storage as a transmission asset?
     Group by RTO, summarize each one's angle, and cite document titles + dates."
   - "Build a table of capacity-accreditation activity by RTO over the last few months."
   - "What pumped-storage-specific items appeared, and in which committees?"
   - "Which stakeholder organizations are most active on energy storage, by RTO?"

## Pulling original source material

Every record includes a \`url\` pointing to the ORIGINAL document on the RTO/ISO's
own website (agenda PDFs, presentations, reports, etc.). These links are part of
this package — the source material is already referenced here, so you do NOT need
to search the web to locate it.

When a question can't be fully answered from \`ai_summary\` / \`relevance_reason\`,
or when the user wants more depth, exact figures, direct quotes, or detail beyond
the summary:

1. Find the relevant record(s) and take the \`url\` field.
2. Open / fetch that \`url\` directly to read the original document.
3. Answer from the source and cite the \`url\`.

Do NOT tell the user to "go find the source material" on their own, and do NOT
start a fresh web search for a document — the authoritative link is already in
the data you were given. Treat \`ai_summary\` as a starting point, not a ceiling:
the linked source document is the ground truth, and you can always go get it.

## Important caveats

- \`ai_summary\` and \`relevance_reason\` are AI-generated (Claude screening) and may
  contain errors. Treat them as a research aid, not an official record — verify
  against the source \`url\` before relying on anything.
- This is the hydro-relevant subset only; non-hydro market activity is excluded
  by design.
- The corpus refreshes automatically as the calendar's pipeline runs, so a newer
  export may include more recent documents.
`;
  }

  // Fetch the two corpus files, bundle them with a generated CLAUDE.md, and
  // trigger a single .zip download. Returns a promise; throws on fetch failure.
  window.downloadCorpusZip = async function () {
    const t = Date.now();
    const base = "../rto-docs/";
    const [jsonRes, csvRes] = await Promise.all([
      fetch(`${base}rto_hydro_corpus.json?t=${t}`),
      fetch(`${base}rto_hydro_corpus.csv?t=${t}`),
    ]);
    if (!jsonRes.ok || !csvRes.ok) {
      throw new Error("Corpus files not found on server.");
    }
    const [jsonText, csvText] = await Promise.all([jsonRes.text(), csvRes.text()]);

    let total = 0;
    const byRto = {};
    try {
      const recs = JSON.parse(jsonText);
      total = recs.length;
      for (const r of recs) byRto[r.rto] = (byRto[r.rto] || 0) + 1;
    } catch (_) { /* readme still works without counts */ }

    const dateStr = new Date().toISOString().slice(0, 10);
    const readme = buildCorpusReadme({ dateStr, total, byRto });

    const blob = buildZipBlob([
      { name: "CLAUDE.md", content: readme },
      { name: "rto_hydro_corpus.json", content: jsonText },
      { name: "rto_hydro_corpus.csv", content: csvText },
    ]);

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nha-rto-hydro-corpus-${dateStr}.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  window.buildZipBlob = buildZipBlob;
  window.buildCorpusReadme = buildCorpusReadme;

  // ── Corpus helpers for the Export modal (client-side filtering) ───────────

  const CORPUS_CSV_COLUMNS = [
    "rto", "meeting_date", "committee", "meeting_title", "doc_type", "title",
    "posted_date", "relevance_reason", "initiatives", "stakeholders",
    "ai_summary", "url",
  ];
  function _csvCell(v) {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function corpusRowToCsv(rec) {
    return CORPUS_CSV_COLUMNS.map((c) => {
      let v = rec[c];
      if (c === "initiatives" || c === "stakeholders") {
        v = Array.isArray(v) ? v.join("; ") : (v || "");
      }
      return _csvCell(v);
    }).join(",");
  }
  // BOM + CRLF rows, matching the server-side utf-8-sig CSV for Excel parity.
  function corpusToCsv(records) {
    const out = [CORPUS_CSV_COLUMNS.join(",")];
    for (const r of records) out.push(corpusRowToCsv(r));
    return "﻿" + out.join("\r\n") + "\r\n";
  }

  // Fetch + cache the full corpus records once (the modal filters in memory).
  window.loadHydroCorpus = async function () {
    if (window.HYDRO_CORPUS) return window.HYDRO_CORPUS;
    const res = await fetch(`../rto-docs/rto_hydro_corpus.json?t=${Date.now()}`);
    if (!res.ok) throw new Error("Corpus files not found on server.");
    window.HYDRO_CORPUS = await res.json();
    return window.HYDRO_CORPUS;
  };

  window.CORPUS_CSV_HEADER = CORPUS_CSV_COLUMNS.join(",");
  window.corpusRowToCsv = corpusRowToCsv;
  window.corpusToCsv = corpusToCsv;
})();
