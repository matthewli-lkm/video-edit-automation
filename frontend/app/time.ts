export const MINIMUM_CLIP_GAP_SECONDS = 0.1;

function roundToTenths(value: number) {
  return Math.round(value * 10) / 10;
}

export function formatTimestamp(seconds: number) {
  const safe = Math.max(0, roundToTenths(Number.isFinite(seconds) ? seconds : 0));
  const minutes = Math.floor(safe / 60);
  const remainder = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainder
    .toFixed(1)
    .padStart(4, "0")}`;
}

export function parseTimestamp(value: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;

  if (!normalized.includes(":")) {
    const seconds = Number(normalized);
    return Number.isFinite(seconds) && seconds >= 0
      ? roundToTenths(seconds)
      : null;
  }

  const match = normalized.match(/^(\d+):([0-5]?\d(?:\.\d)?)$/);
  if (!match) return null;
  const minutes = Number(match[1]);
  const seconds = Number(match[2]);
  if (!Number.isFinite(minutes) || !Number.isFinite(seconds) || seconds >= 60) {
    return null;
  }
  return roundToTenths(minutes * 60 + seconds);
}

export function clampBoundary(
  field: "start" | "end",
  value: number,
  start: number,
  end: number,
  sourceDuration: number,
) {
  const duration = Math.max(sourceDuration, MINIMUM_CLIP_GAP_SECONDS);
  if (field === "start") {
    return Math.min(
      Math.max(0, roundToTenths(value)),
      Math.max(0, roundToTenths(end - MINIMUM_CLIP_GAP_SECONDS)),
    );
  }
  return Math.max(
    roundToTenths(start + MINIMUM_CLIP_GAP_SECONDS),
    Math.min(duration, roundToTenths(value)),
  );
}

export function moveClipRange(
  start: number,
  end: number,
  deltaSeconds: number,
  sourceDuration: number,
) {
  const safeDuration = Math.max(sourceDuration, MINIMUM_CLIP_GAP_SECONDS);
  const clipDuration = Math.min(
    safeDuration,
    Math.max(MINIMUM_CLIP_GAP_SECONDS, roundToTenths(end - start)),
  );
  const nextStart = Math.min(
    Math.max(0, roundToTenths(start + deltaSeconds)),
    Math.max(0, roundToTenths(safeDuration - clipDuration)),
  );
  return {
    start: nextStart,
    end: roundToTenths(nextStart + clipDuration),
  };
}

export function createBoundaryWindow(
  start: number,
  end: number,
  sourceDuration: number,
  contextSeconds = 30,
) {
  const safeDuration = Math.max(sourceDuration, end);
  return {
    start: Math.max(0, roundToTenths(start - contextSeconds)),
    end: Math.min(safeDuration, roundToTenths(end + contextSeconds)),
  };
}

export function positionRangeInWindow(
  start: number,
  end: number,
  windowStart: number,
  windowEnd: number,
) {
  const duration = Math.max(MINIMUM_CLIP_GAP_SECONDS, windowEnd - windowStart);
  const left = Math.max(0, Math.min(100, ((start - windowStart) / duration) * 100));
  const right = Math.max(left, Math.min(100, ((end - windowStart) / duration) * 100));
  return {
    left,
    width: right - left,
  };
}
