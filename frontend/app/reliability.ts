export type RestorableRenderJob = {
  id: string;
  plan_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  preset: { profile: "preview" | "final" };
  created_at: string;
};

export function restoreRenderJobs<T extends RestorableRenderJob>(
  jobs: T[],
  planId: string | undefined,
): { preview: T | null; final: T | null } {
  if (!planId) return { preview: null, final: null };
  const relevant = jobs
    .filter((job) => job.plan_id === planId)
    .sort((left, right) => right.created_at.localeCompare(left.created_at));
  return {
    preview:
      relevant.find((job) => job.preset.profile === "preview") ?? null,
    final:
      relevant.find((job) => job.preset.profile === "final") ?? null,
  };
}

export function restoreAssetId(
  assetIds: string[],
  preferredAssetId: string | undefined,
): string {
  return assetIds.includes(preferredAssetId ?? "")
    ? (preferredAssetId as string)
    : (assetIds[0] ?? "");
}

export function restoreWorkflow<T extends { review_session_id?: string }>(
  workflows: T[],
  reviewSessionId: string | undefined,
): T | null {
  if (!reviewSessionId) return null;
  return (
    workflows.find((workflow) => workflow.review_session_id === reviewSessionId) ??
    null
  );
}

export type PlaybackSummary = {
  mode: "original" | "proxy";
  status: "not_started" | "queued" | "running" | "ready" | "failed";
  error?: string | null;
};

export function playbackStatusCopy(playback: PlaybackSummary | null): string {
  if (!playback) return "Playback status unavailable";
  if (playback.mode === "original") return "Original is browser-ready";
  if (playback.status === "ready") return "Browser preview ready";
  if (playback.status === "queued") return "Browser preview queued";
  if (playback.status === "running") return "Preparing browser preview";
  if (playback.status === "failed") {
    return playback.error ?? "Browser preview needs a retry";
  }
  return "Browser preview not prepared";
}
