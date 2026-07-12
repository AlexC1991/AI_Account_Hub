// Pure Codex rate-limit selection and normalization helpers. Keeping these
// separate from the app-server process makes the provider payload rules easy
// to regression-test without launching Codex or touching account credentials.

export function safeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function epochToIso(value) {
  const n = safeNumber(value);
  if (n === null) return null;
  const millis = n > 1_000_000_000_000 ? n : n * 1000;
  const date = new Date(millis);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function normalizeWindow(window, fallbackLabel) {
  if (!window) return null;
  const duration = safeNumber(window.windowDurationMins);
  const usedPercent = safeNumber(window.usedPercent);
  let label = fallbackLabel;
  if (duration === 300) label = "5h";
  else if (duration === 10080) label = "Weekly";
  else if (duration) label = `${duration}m`;
  return {
    label,
    usedPercent,
    windowDurationMins: duration,
    resetsAtIso: epochToIso(window.resetsAt),
  };
}

export function chooseSnapshot(rateLimits) {
  const byId = rateLimits?.rateLimitsByLimitId;
  return byId?.codex || rateLimits?.rateLimits || null;
}

function median(values) {
  const ordered = values
    .filter((value) => typeof value === "number" && Number.isFinite(value))
    .sort((left, right) => left - right);
  if (!ordered.length) return null;
  return ordered[Math.floor(ordered.length / 2)];
}

function snapshotDistance(snapshot, primaryMedian, secondaryMedian) {
  let distance = 0;
  let compared = 0;
  for (const [window, target] of [
    [snapshot?.primary, primaryMedian],
    [snapshot?.secondary, secondaryMedian],
  ]) {
    if (target === null) continue;
    const value = safeNumber(window?.usedPercent);
    distance += value === null ? 200 : Math.abs(value - target);
    compared += 1;
  }
  return compared ? distance : 0;
}

export function selectRateLimitConsensus(samples) {
  const available = samples
    .map((value, index) => ({ value, index, snapshot: chooseSnapshot(value) }))
    .filter((item) => item.snapshot);
  if (!available.length) {
    return {
      value: samples.at(-1) ?? null,
      diagnostics: { sampleCount: samples.length, usableSamples: 0 },
    };
  }

  const blockedCount = available.filter(
    (item) => Boolean(item.snapshot.rateLimitReachedType),
  ).length;
  const majorityBlocked = blockedCount > available.length / 2;
  const candidates = available.filter(
    (item) => Boolean(item.snapshot.rateLimitReachedType) === majorityBlocked,
  );
  const primaryMedian = median(
    candidates.map((item) => safeNumber(item.snapshot.primary?.usedPercent)),
  );
  const secondaryMedian = median(
    candidates.map((item) => safeNumber(item.snapshot.secondary?.usedPercent)),
  );
  const selected = candidates.reduce((best, item) => {
    const distance = snapshotDistance(item.snapshot, primaryMedian, secondaryMedian);
    const newerTie = best && distance === best.distance && item.index > best.item.index;
    if (!best || distance < best.distance || newerTie) return { item, distance };
    return best;
  }, null).item;

  return {
    value: selected.value,
    diagnostics: {
      sampleCount: samples.length,
      usableSamples: available.length,
      blockedSamples: blockedCount,
      selectedBlocked: majorityBlocked,
      selectedIndex: selected.index,
      disagreement: blockedCount > 0 && blockedCount < available.length,
    },
  };
}

export function normalizeRateLimits(rateLimits) {
  const snapshot = chooseSnapshot(rateLimits);
  const primary = normalizeWindow(snapshot?.primary, "Primary");
  const secondary = normalizeWindow(snapshot?.secondary, "Secondary");
  const windows = [primary, secondary].filter(Boolean);
  const shortWindow =
    windows.find((item) => item.windowDurationMins === 300) ||
    windows.find((item) => item.windowDurationMins !== null && item.windowDurationMins <= 360) ||
    null;
  const weeklyWindow =
    windows.find((item) => item.windowDurationMins === 10080) ||
    windows.find((item) => item.windowDurationMins !== null && item.windowDurationMins >= 7 * 24 * 60) ||
    null;

  // Older servers may omit durations. Preserve their primary/secondary order,
  // but never assign the same provider window to both Hub limit slots.
  const allDurationsMissing = windows.length > 0 && windows.every(
    (item) => item.windowDurationMins === null,
  );
  const resolvedShort = shortWindow || (allDurationsMissing ? primary : null);
  const resolvedWeekly = weeklyWindow || (
    allDurationsMissing && secondary && secondary !== resolvedShort ? secondary : null
  );

  return {
    limitId: snapshot?.limitId ?? null,
    limitName: snapshot?.limitName ?? null,
    planType: snapshot?.planType ?? null,
    rateLimitReachedType: snapshot?.rateLimitReachedType ?? null,
    shortWindow: resolvedShort,
    weeklyWindow: resolvedWeekly,
    credits: snapshot?.credits ?? null,
    individualLimit: snapshot?.individualLimit ?? null,
    rateLimitResetCredits: rateLimits?.rateLimitResetCredits ?? null,
  };
}
