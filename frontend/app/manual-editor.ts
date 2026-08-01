export type ManualClip = {
  id: string;
  title: string;
  start: number;
  end: number;
  selected: boolean;
};

const DEFAULT_PLAY_SECONDS = 30;
const DEFAULT_PREROLL_SECONDS = 12;

export function createPlayAroundPlayhead(
  playhead: number,
  sourceDuration: number,
  index: number,
  id: string,
): ManualClip {
  const safeDuration = Math.max(0.1, sourceDuration);
  const duration = Math.min(DEFAULT_PLAY_SECONDS, safeDuration);
  const desiredStart = Math.max(0, playhead - DEFAULT_PREROLL_SECONDS);
  const start = Math.min(desiredStart, Math.max(0, safeDuration - duration));
  const end = Math.min(safeDuration, start + duration);
  return {
    id,
    title: `Play ${index + 1}`,
    start: Number(start.toFixed(1)),
    end: Number(end.toFixed(1)),
    selected: true,
  };
}

export function selectedManualClips(clips: ManualClip[]): ManualClip[] {
  return clips
    .filter((clip) => clip.selected)
    .sort((left, right) => left.start - right.start || left.end - right.end);
}

export function validateManualClips(
  clips: ManualClip[],
  sourceDuration: number,
): string | null {
  const selected = selectedManualClips(clips);
  if (!selected.length) return "Select at least one play to continue";
  for (let index = 0; index < selected.length; index += 1) {
    const clip = selected[index];
    if (clip.start < 0 || clip.end <= clip.start) {
      return `${clip.title} has an invalid In and Out range`;
    }
    if (clip.end > sourceDuration) {
      return `${clip.title} ends after the source recording`;
    }
    if (index > 0 && clip.start < selected[index - 1].end) {
      return `${selected[index - 1].title} overlaps ${clip.title}. Trim one of them before continuing.`;
    }
  }
  return null;
}

export function totalManualDuration(clips: ManualClip[]): number {
  return selectedManualClips(clips).reduce(
    (total, clip) => total + clip.end - clip.start,
    0,
  );
}
