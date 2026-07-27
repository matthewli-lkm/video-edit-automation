"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  clampBoundary,
  createBoundaryWindow,
  formatTimestamp,
  parseTimestamp,
} from "./time";
import { deriveWorkflowSteps } from "./workflow";
import {
  playbackStatusCopy,
  restoreAssetId,
  restoreRenderJobs,
  restoreWorkflow,
} from "./reliability";
import type { components } from "./generated/api-schema";

type Mode = "demo" | "live";
type EditWorkflow = "manual" | "automation";
type AiAssistance = "off" | "review" | "full";
type Decision = "pending" | "accept" | "reject" | "adjust";
type PreviewMode = "source" | "cut" | "reel";
type TrimPoint = { start: number; end: number };
type TrimHistory = { entries: TrimPoint[]; cursor: number };
type ManualClipDraft = {
  id: string;
  title: string;
  start: number;
  end: number;
};
type ApiSchemas = components["schemas"];
type WithId<T extends { id?: string }> = Omit<T, "id"> & { id: string };

type Candidate = {
  id: string;
  index: number;
  title: string;
  start: number;
  end: number;
  score: number;
  signalIds: string[];
  labels: string[];
  decision: Decision;
};

type Project = WithId<ApiSchemas["Project"]>;
type Asset = WithId<ApiSchemas["MediaAsset"]>;
type Plan = WithId<ApiSchemas["EditPlan"]>;

type ReviewDecision = {
  id: string;
  action: Decision | "missed_highlight";
  candidate_id?: string;
  start_seconds?: number;
  end_seconds?: number;
  event_name?: string;
  revision: number;
};

type ReviewSnapshot = {
  session: {
    id: string;
    plan_id: string;
    asset_id: string;
    analyzer: string;
    game_profile_id: string;
    candidates: Array<{
      id: string;
      plan_segment_index: number;
      start_seconds: number;
      end_seconds: number;
      score: number;
      signal_ids: string[];
      labels: string[];
    }>;
    signals: Array<{
      id: string;
      timestamp_seconds: number;
      signal_type: string;
      event_name?: string;
      confidence: number;
      source?: string;
    }>;
  };
  decisions: ReviewDecision[];
  latest_candidate_decisions: ReviewDecision[];
  metrics: {
    candidate_count: number;
    reviewed_candidate_count: number;
    pending_candidate_count: number;
    accepted_candidate_count: number;
    adjusted_candidate_count: number;
    rejected_candidate_count: number;
    review_complete: boolean;
    precision?: number;
    recall?: number;
  };
};

type HumanReview = {
  state: {
    current_plan_id: string;
    active_approval_id?: string;
    approvals: Array<{ id: string; plan_id: string; plan_version: number }>;
  };
  plan: Plan;
  validation: {
    valid: boolean;
    total_duration_seconds: number;
    issues: Array<{ severity: string; message: string }>;
  };
};

type Workflow = {
  id: string;
  review_session_id?: string;
  state:
    | "queued"
    | "rendering"
    | "reviewing"
    | "revision_required"
    | "approved"
    | "human_review_required"
    | "technical_failure";
  reviewer_name: string;
  current_plan_id: string;
  rounds: Array<{
    round_number: number;
    verdict?: {
      outcome: string;
      confidence: number;
      summary: string;
      corrections: Array<{ action: string; reason: string }>;
    };
  }>;
  error?: string;
};

type RenderJob = WithId<ApiSchemas["Job"]>;
type AssetPlayback = ApiSchemas["AssetPlayback"];
type HealthStatus = ApiSchemas["HealthResponse"];
type DesktopDiagnostics = ApiSchemas["DesktopDiagnosticsResponse"];

type CutroomDesktopBridge = {
  bootstrap: () => Promise<{ apiUrl: string }>;
  selectMedia: (defaultPath?: string) => Promise<{ path: string } | null>;
  selectFolder: () => Promise<{ path: string; name: string } | null>;
  revealOutput: (outputPath: string) => Promise<boolean>;
};

declare global {
  interface Window {
    cutroomDesktop?: CutroomDesktopBridge;
  }
}

const DEMO_CANDIDATES: Candidate[] = [
  {
    id: "demo-1",
    index: 0,
    title: "River turnaround",
    start: 628.4,
    end: 655.2,
    score: 0.94,
    signalIds: ["ocr-kill-14", "ocr-kill-15"],
    labels: ["champion kill", "team fight"],
    decision: "accept",
  },
  {
    id: "demo-2",
    index: 1,
    title: "Baron pit teamfight",
    start: 1014.8,
    end: 1056.6,
    score: 0.88,
    signalIds: ["ocr-kill-22", "ocr-multikill-04"],
    labels: ["multi kill", "team fight"],
    decision: "adjust",
  },
  {
    id: "demo-3",
    index: 2,
    title: "Base defence",
    start: 1460.1,
    end: 1492.3,
    score: 0.76,
    signalIds: ["ocr-kill-29"],
    labels: ["champion kill"],
    decision: "pending",
  },
  {
    id: "demo-4",
    index: 3,
    title: "Nexus cleanup",
    start: 1818.7,
    end: 1832.5,
    score: 0.71,
    signalIds: ["ocr-kill-34"],
    labels: ["champion kill"],
    decision: "pending",
  },
];

const storageKey = "vea-gaming-console-v1";

function formatTime(seconds: number) {
  const safe = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(safe / 60);
  return `${minutes}:${String(safe % 60).padStart(2, "0")}`;
}

function formatStorage(bytes: number) {
  if (bytes < 1024 ** 3) return `${Math.round(bytes / 1024 ** 2)} MB free`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB free`;
}

function statusLabel(value: string) {
  return value.replaceAll("_", " ");
}

function sameTrim(left: TrimPoint, right: TrimPoint) {
  return (
    Math.abs(left.start - right.start) < 0.05 &&
    Math.abs(left.end - right.end) < 0.05
  );
}

async function requestJson<T>(
  baseUrl: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    throw new Error(
      "Cutroom lost its local connection. Wait a moment, then try again.",
    );
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export default function Home() {
  const [mode, setMode] = useState<Mode>("demo");
  const [editWorkflow, setEditWorkflow] =
    useState<EditWorkflow>("automation");
  const [aiAssistance, setAiAssistance] =
    useState<AiAssistance>("off");
  const [apiUrl, setApiUrl] = useState("http://127.0.0.1:8765");
  const [connection, setConnection] = useState<
    "idle" | "checking" | "connected" | "failed"
  >("idle");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("League highlights");
  const [projectFolders, setProjectFolders] = useState<Record<string, string>>(
    {},
  );
  const [assets, setAssets] = useState<Asset[]>([]);
  const [assetId, setAssetId] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [showSetup, setShowSetup] = useState(true);
  const [targetDuration, setTargetDuration] = useState(180);
  const [maxHighlights, setMaxHighlights] = useState(8);
  const [demoCandidates, setDemoCandidates] =
    useState<Candidate[]>(DEMO_CANDIDATES);
  const [demoApproved, setDemoApproved] = useState(false);
  const [demoPlanVersion, setDemoPlanVersion] = useState(3);
  const [selectedId, setSelectedId] = useState(DEMO_CANDIDATES[1].id);
  const [review, setReview] = useState<ReviewSnapshot | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [humanReview, setHumanReview] = useState<HumanReview | null>(null);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<DesktopDiagnostics | null>(null);
  const [previewJobId, setPreviewJobId] = useState("");
  const [previewJob, setPreviewJob] = useState<RenderJob | null>(null);
  const [finalJob, setFinalJob] = useState<RenderJob | null>(null);
  const [playback, setPlayback] = useState<AssetPlayback | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("Demo workspace ready");
  const [error, setError] = useState("");
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [desktopMode, setDesktopMode] = useState(false);
  const [previewMode, setPreviewMode] = useState<PreviewMode>("cut");
  const [boundaryDraft, setBoundaryDraft] = useState({
    start: formatTimestamp(DEMO_CANDIDATES[1].start),
    end: formatTimestamp(DEMO_CANDIDATES[1].end),
  });
  const [boundaryError, setBoundaryError] = useState("");
  const [trimDrafts, setTrimDrafts] = useState<Record<string, TrimPoint>>({});
  const [trimHistory, setTrimHistory] = useState<Record<string, TrimHistory>>(
    {},
  );
  const [manualSavedTrims, setManualSavedTrims] = useState<
    Record<string, TrimPoint>
  >({});
  const [missedDraft, setMissedDraft] = useState({
    event: "Missed highlight",
    start: "",
    end: "",
  });
  const [manualClips, setManualClips] = useState<ManualClipDraft[]>([]);
  const [manualDraft, setManualDraft] = useState({
    title: "Highlight",
    start: "",
    end: "",
  });
  const [planRevisionNeeded, setPlanRevisionNeeded] = useState(false);
  const [playheadSeconds, setPlayheadSeconds] = useState(
    DEMO_CANDIDATES[1].start,
  );
  const [boundaryWindow, setBoundaryWindow] = useState(() =>
    createBoundaryWindow(
      DEMO_CANDIDATES[1].start,
      DEMO_CANDIDATES[1].end,
      1938,
    ),
  );
  const previewRef = useRef<HTMLVideoElement>(null);
  const playbackRequestRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const saved = window.localStorage.getItem(storageKey);
      if (!saved) return;
      try {
        const state = JSON.parse(saved) as {
          apiUrl?: string;
          targetDuration?: number;
          maxHighlights?: number;
          projectId?: string;
          assetId?: string;
          projectFolders?: Record<string, string>;
          editWorkflow?: EditWorkflow;
          aiAssistance?: AiAssistance;
        };
        if (state.apiUrl && !window.cutroomDesktop) setApiUrl(state.apiUrl);
        if (state.targetDuration) setTargetDuration(state.targetDuration);
        if (state.maxHighlights) setMaxHighlights(state.maxHighlights);
        if (state.projectId) setProjectId(state.projectId);
        if (state.assetId) setAssetId(state.assetId);
        if (state.projectFolders) setProjectFolders(state.projectFolders);
        if (["manual", "automation"].includes(state.editWorkflow ?? "")) {
          setEditWorkflow(state.editWorkflow as EditWorkflow);
        }
        if (["off", "review", "full"].includes(state.aiAssistance ?? "")) {
          setAiAssistance(state.aiAssistance as AiAssistance);
        }
      } catch {
        window.localStorage.removeItem(storageKey);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const bridge = window.cutroomDesktop;
    if (!bridge) return;
    void bridge
      .bootstrap()
      .then(({ apiUrl: desktopApiUrl }) => {
        setDesktopMode(true);
        setApiUrl(desktopApiUrl);
        setMode("live");
        return connect(desktopApiUrl);
      })
      .catch((nextError) => {
        setConnection("failed");
        setError(
          nextError instanceof Error
            ? nextError.message
            : "The desktop backend did not start",
        );
      });
    // The native bridge is immutable for the lifetime of the desktop renderer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify({
        apiUrl,
        targetDuration,
        maxHighlights,
        projectId,
        assetId,
        projectFolders,
        editWorkflow,
        aiAssistance,
      }),
    );
  }, [
    aiAssistance,
    apiUrl,
    assetId,
    editWorkflow,
    maxHighlights,
    projectFolders,
    projectId,
    targetDuration,
  ]);

  useEffect(() => {
    if (
      mode !== "live" ||
      !workflow ||
      ["approved", "human_review_required", "technical_failure"].includes(
        workflow.state,
      )
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void requestJson<Workflow>(
        apiUrl,
        `/api/v1/projects/${projectId}/gaming/agent-workflows/${workflow.id}`,
      )
        .then((nextWorkflow) => {
          setWorkflow(nextWorkflow);
          if (
            ["approved", "human_review_required", "technical_failure"].includes(
              nextWorkflow.state,
            )
          ) {
            setNotice(`Agent 2 ${statusLabel(nextWorkflow.state)}`);
          }
        })
        .catch((nextError: unknown) =>
          setError(
            nextError instanceof Error
              ? nextError.message
              : "Reviewer status could not be refreshed",
          ),
        );
    }, 1200);
    return () => window.clearInterval(timer);
  }, [apiUrl, mode, projectId, workflow]);

  useEffect(() => {
    if (mode !== "live") return;
    const activeJobs = [previewJob, finalJob].filter(
      (job): job is RenderJob =>
        Boolean(job && ["queued", "running"].includes(job.status)),
    );
    if (!activeJobs.length) return;
    const timer = window.setInterval(() => {
      void Promise.all(
        activeJobs.map((job) =>
          requestJson<RenderJob>(apiUrl, `/api/v1/jobs/${job.id}`),
        ),
      )
        .then((jobs) => {
          for (const job of jobs) {
            if (job.preset.profile === "preview") {
              setPreviewJob(job);
              if (job.status === "succeeded") setPreviewJobId(job.id);
            } else {
              setFinalJob(job);
            }
            if (job.status === "failed") {
              setNotice(job.error ?? `${statusLabel(job.preset.profile)} render failed`);
            }
          }
        })
        .catch((nextError: unknown) =>
          setError(
            nextError instanceof Error
              ? nextError.message
              : "Render status could not be refreshed",
          ),
        );
    }, 1200);
    return () => window.clearInterval(timer);
  }, [
    apiUrl,
    finalJob,
    mode,
    previewJob,
  ]);

  const liveCandidates = useMemo<Candidate[]>(() => {
    if (!review) return [];
    const decisions = new Map(
      review.latest_candidate_decisions
        .filter((item) => item.candidate_id)
        .map((item) => [item.candidate_id as string, item]),
    );
    const detected = review.session.candidates.map((candidate) => {
      const decision = decisions.get(candidate.id);
      const eventNames = candidate.signal_ids
        .map(
          (signalId) =>
            review.session.signals.find((signal) => signal.id === signalId)
              ?.event_name,
        )
        .filter(Boolean) as string[];
      const draft = trimDrafts[candidate.id];
      return {
        id: candidate.id,
        index: candidate.plan_segment_index,
        title:
          eventNames.map(statusLabel).join(" + ") ||
          `Highlight ${candidate.plan_segment_index + 1}`,
        start:
          draft?.start ?? decision?.start_seconds ?? candidate.start_seconds,
        end: draft?.end ?? decision?.end_seconds ?? candidate.end_seconds,
        score: candidate.score,
        signalIds: candidate.signal_ids,
        labels: candidate.labels,
        decision: (decision?.action as Decision | undefined) ?? "pending",
      };
    });
    const manualPlanSegments =
      plan?.segments.filter(
        (segment) =>
          segment.highlight_signal_ids.length === 0 &&
          segment.purpose.startsWith("Manual"),
      ) ?? [];
    const missed = review.decisions
      .filter(
        (decision) =>
          decision.action === "missed_highlight" &&
          decision.start_seconds !== undefined &&
          decision.end_seconds !== undefined,
      )
      .map((decision, index): Candidate => {
        const id = `manual-${decision.id}`;
        const draft = trimDrafts[id];
        const saved = manualSavedTrims[id];
        const planSegment = manualPlanSegments[index];
        return {
          id,
          index: detected.length + index,
          title: decision.event_name
            ? `Manual · ${statusLabel(decision.event_name)}`
            : `Manual highlight ${index + 1}`,
          start:
            draft?.start ??
            saved?.start ??
            planSegment?.source_in_seconds ??
            decision.start_seconds ??
            0,
          end:
            draft?.end ??
            saved?.end ??
            planSegment?.source_out_seconds ??
            decision.end_seconds ??
            0.1,
          score: 1,
          signalIds: [],
          labels: ["manual review"],
          decision: "accept",
        };
      });
    return [...detected, ...missed];
  }, [manualSavedTrims, plan, review, trimDrafts]);

  const candidates = useMemo(
    () =>
      (mode === "demo" ? demoCandidates : liveCandidates).map((candidate) => {
        const draft = trimDrafts[candidate.id];
        return draft ? { ...candidate, ...draft } : candidate;
      }),
    [demoCandidates, liveCandidates, mode, trimDrafts],
  );
  const selected =
    candidates.find((candidate) => candidate.id === selectedId) ??
    candidates[0];
  const selectedIsManual = selected?.id.startsWith("manual-") ?? false;
  const acceptedCount = candidates.filter((candidate) =>
    ["accept", "adjust"].includes(candidate.decision),
  ).length;
  const rejectedCount = candidates.filter(
    (candidate) => candidate.decision === "reject",
  ).length;
  const pendingCount = candidates.filter(
    (candidate) => candidate.decision === "pending",
  ).length;
  const hasUnsavedTrims = Object.keys(trimDrafts).length > 0;
  const reelDuration = candidates
    .filter((candidate) => candidate.decision !== "reject")
    .reduce((total, candidate) => total + candidate.end - candidate.start, 0);
  const isApproved =
    mode === "demo"
      ? demoApproved
      : Boolean(humanReview?.state.active_approval_id);
  const activeAsset = assets.find((asset) => asset.id === assetId);
  const activeFolderPath =
    Object.entries(projectFolders).find(([, id]) => id === projectId)?.[0] ??
    "";
  const sourceDuration =
    mode === "demo"
      ? 1938
      : (activeAsset?.duration_seconds ??
        Math.max(1, ...candidates.map((candidate) => candidate.end)));
  const maximumMinutes = Math.floor(targetDuration / 60);
  const maximumSeconds = Math.round(targetDuration % 60);
  const boundaryWindowStart = Math.min(boundaryWindow.start, sourceDuration);
  const boundaryWindowEnd = Math.max(
    boundaryWindowStart + 0.1,
    Math.min(boundaryWindow.end, sourceDuration),
  );
  const boundaryWindowDuration = boundaryWindowEnd - boundaryWindowStart;
  const sourceMediaUrl =
    mode === "live" &&
    projectId &&
    assetId &&
    playback?.status === "ready"
      ? `${apiUrl.replace(/\/$/, "")}/api/v1/projects/${projectId}/assets/${assetId}/playback/media`
      : "";
  const reelMediaUrl =
    mode === "live" &&
    (finalJob?.status === "succeeded" ? finalJob.id : previewJobId)
      ? `${apiUrl.replace(/\/$/, "")}/api/v1/jobs/${
          finalJob?.status === "succeeded" ? finalJob.id : previewJobId
        }/media`
      : "";
  const finalDownloadUrl =
    mode === "live" && finalJob?.status === "succeeded"
      ? `${apiUrl.replace(/\/$/, "")}/api/v1/jobs/${finalJob.id}/download`
      : "";
  const activeMediaUrl = previewMode === "reel" ? reelMediaUrl : sourceMediaUrl;
  const activeTrimHistory = selected ? trimHistory[selected.id] : undefined;
  const canUndoTrim = Boolean(activeTrimHistory && activeTrimHistory.cursor > 0);
  const canRedoTrim = Boolean(
    activeTrimHistory &&
      activeTrimHistory.cursor < activeTrimHistory.entries.length - 1,
  );
  const sourceReady = mode === "demo" || Boolean(activeAsset);
  const briefReady =
    editWorkflow === "automation" || manualClips.length > 0;
  const analysisReady = candidates.length > 0;
  const reviewReady =
    analysisReady &&
    pendingCount === 0 &&
    !hasUnsavedTrims &&
    !planRevisionNeeded &&
    isApproved;
  const exportReady = finalJob?.status === "succeeded";
  const reviewerConfigured =
    mode === "demo" || health?.highlight_reviewer === "ready";
  const reviewerAvailable =
    aiAssistance === "review" && reviewerConfigured;
  const automationReady =
    mode === "demo" || diagnostics?.tesseract.status === "ready";
  const setupNeeded =
    mode === "live" && connection === "connected" && showSetup;
  const workflowSteps = deriveWorkflowSteps({
    sourceReady,
    briefReady,
    analysisReady,
    decisionsComplete: pendingCount === 0,
    hasUnsavedChanges: hasUnsavedTrims,
    planRevisionNeeded,
    humanApproved: isApproved,
    finalRenderSucceeded: exportReady,
  });
  const startPercent = selected
    ? Math.min(
        100,
        Math.max(
          0,
          ((selected.start - boundaryWindowStart) / boundaryWindowDuration) * 100,
        ),
      )
    : 0;
  const endPercent = selected
    ? Math.min(
        100,
        Math.max(
          0,
          ((selected.end - boundaryWindowStart) / boundaryWindowDuration) * 100,
        ),
      )
    : 0;

  async function connect(requestedApiUrl?: string) {
    const connectionUrl = requestedApiUrl ?? apiUrl;
    if (requestedApiUrl) setApiUrl(requestedApiUrl);
    setConnection("checking");
    setError("");
    try {
      const nextHealth = await requestJson<HealthStatus>(
        connectionUrl,
        "/healthz",
      );
      const [nextProjects, nextDiagnostics] = await Promise.all([
        requestJson<Project[]>(connectionUrl, "/api/v1/projects"),
        requestJson<DesktopDiagnostics>(
          connectionUrl,
          "/api/v1/desktop/diagnostics",
        ).catch(() => null),
      ]);
      setHealth(nextHealth);
      setDiagnostics(nextDiagnostics);
      setProjects(nextProjects);
      setConnection("connected");
      setMode("live");
      setNotice("Local backend connected");
    } catch (nextError) {
      setConnection("failed");
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Could not reach the local backend",
      );
    }
  }

  async function chooseSource() {
    if (!projectId) {
      setError("Create or open a project before adding a recording");
      return;
    }
    const selected = await window.cutroomDesktop?.selectMedia(
      activeFolderPath || undefined,
    );
    if (!selected) return;
    await importRecording(selected.path);
  }

  async function revealFinalOutput() {
    if (!finalJob?.output_path || !window.cutroomDesktop) return;
    setError("");
    try {
      await window.cutroomDesktop.revealOutput(String(finalJob.output_path));
      setNotice("Final output shown in Finder");
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Could not open the output folder",
      );
    }
  }

  async function preparePlayback(nextProjectId: string, nextAssetId: string) {
    const requestId = ++playbackRequestRef.current;
    setPlayback(null);
    try {
      let nextPlayback = await requestJson<AssetPlayback>(
        apiUrl,
        `/api/v1/projects/${nextProjectId}/assets/${nextAssetId}/playback/prepare`,
        { method: "POST" },
      );
      if (requestId !== playbackRequestRef.current) return;
      setPlayback(nextPlayback);
      for (
        let attempt = 0;
        ["queued", "running"].includes(nextPlayback.status) && attempt < 300;
        attempt += 1
      ) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        nextPlayback = await requestJson<AssetPlayback>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/assets/${nextAssetId}/playback`,
        );
        if (requestId !== playbackRequestRef.current) return;
        setPlayback(nextPlayback);
      }
      if (nextPlayback.status === "ready") {
        setNotice(playbackStatusCopy(nextPlayback));
      } else if (nextPlayback.status === "failed") {
        setNotice("Browser preview needs attention");
      }
    } catch (nextError) {
      if (requestId !== playbackRequestRef.current) return;
      setPlayback(null);
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Browser playback could not be prepared",
      );
    }
  }

  async function loadProject(nextProjectId: string, preferredAssetId?: string) {
    playbackRequestRef.current += 1;
    setBusy("Loading project");
    setError("");
    setTrimDrafts({});
    setTrimHistory({});
    setManualSavedTrims({});
    setPlanRevisionNeeded(false);
    setPreviewJobId("");
    setPreviewJob(null);
    setFinalJob(null);
    setPlayback(null);
    try {
      const [nextAssets, reviews, plans, jobs, workflows] = await Promise.all([
        requestJson<Asset[]>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/assets`,
        ),
        requestJson<ReviewSnapshot["session"][]>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/gaming/highlight-reviews`,
        ),
        requestJson<Plan[]>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/edit-plans`,
        ),
        requestJson<RenderJob[]>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/jobs`,
        ),
        requestJson<Workflow[]>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/gaming/agent-workflows`,
        ),
      ]);
      const nextAssetId = restoreAssetId(
        nextAssets.map((asset) => asset.id),
        preferredAssetId,
      );
      setAssets(nextAssets);
      setAssetId(nextAssetId);
      let restoredPlan: Plan | null = null;
      const latestReview = reviews.find((item) => item.asset_id === nextAssetId);
      if (latestReview) {
        const snapshot = await requestJson<ReviewSnapshot>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/gaming/highlight-reviews/${latestReview.id}`,
        );
        setReview(snapshot);
        const firstCandidate = snapshot.session.candidates[0];
        setSelectedId(firstCandidate?.id ?? "");
        if (firstCandidate) {
          setBoundaryDraft({
            start: formatTimestamp(firstCandidate.start_seconds),
            end: formatTimestamp(firstCandidate.end_seconds),
          });
          setBoundaryWindow(
            createBoundaryWindow(
              firstCandidate.start_seconds,
              firstCandidate.end_seconds,
              nextAssets.find((asset) => asset.id === nextAssetId)
                ?.duration_seconds ?? firstCandidate.end_seconds + 30,
            ),
          );
          setPlayheadSeconds(firstCandidate.start_seconds);
        }
        const human = await requestJson<HumanReview>(
          apiUrl,
          `/api/v1/projects/${nextProjectId}/gaming/highlight-reviews/${latestReview.id}/human-review`,
        );
        setHumanReview(human);
        setPlan(human.plan);
        restoredPlan = human.plan;
      } else {
        restoredPlan =
          plans.find((item) =>
            item.segments.some((segment) => segment.asset_id === nextAssetId),
          ) ?? null;
        setPlan(restoredPlan);
        setReview(null);
        setHumanReview(null);
      }
      const restoredJobs = restoreRenderJobs(jobs, restoredPlan?.id);
      setPreviewJob(restoredJobs.preview);
      setPreviewJobId(
        restoredJobs.preview?.status === "succeeded"
          ? restoredJobs.preview.id
          : "",
      );
      setFinalJob(restoredJobs.final);
      setWorkflow(restoreWorkflow(workflows, latestReview?.id));
      setNotice(
        restoredJobs.final
          ? `Project restored · final render ${statusLabel(restoredJobs.final.status)}`
          : "Project state restored",
      );
      if (nextAssetId) {
        void preparePlayback(nextProjectId, nextAssetId);
      }
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Project could not be loaded",
      );
    } finally {
      setBusy("");
    }
  }

  function clearProjectWorkspace() {
    setAssets([]);
    setAssetId("");
    setReview(null);
    setPlan(null);
    setHumanReview(null);
    setPlayback(null);
    setPreviewJob(null);
    setPreviewJobId("");
    setFinalJob(null);
  }

  async function createProjectRecord(name: string) {
    setBusy("Creating project");
    setError("");
    try {
      const project = await requestJson<Project>(apiUrl, "/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setProjects((current) => [...current, project]);
      setProjectId(project.id);
      clearProjectWorkspace();
      return project;
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Create failed");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function createNewProject(event: FormEvent) {
    event.preventDefault();
    const name = projectName.trim();
    if (!name) return;
    const project = await createProjectRecord(name);
    if (!project) return;
    setShowSetup(false);
    setNotice("Project created · add a recording to begin");
  }

  async function openExistingFolder() {
    const selected = await window.cutroomDesktop?.selectFolder();
    if (!selected) return;
    setError("");
    const mappedProject = projects.find(
      (project) => project.id === projectFolders[selected.path],
    );
    let nextProject = mappedProject;
    if (!nextProject) {
      nextProject = await createProjectRecord(selected.name);
      if (!nextProject) return;
      setProjectFolders((current) => ({
        ...current,
        [selected.path]: nextProject.id,
      }));
    } else {
      setProjectId(nextProject.id);
      await loadProject(nextProject.id);
    }
    setShowSetup(false);
    setNotice(
      mappedProject
        ? `Opened ${mappedProject.name}`
        : `Video folder ready · add a recording to begin`,
    );
  }

  async function openExistingWebProject(event: FormEvent) {
    event.preventDefault();
    if (!projectId) {
      setError("Choose an existing project first");
      return;
    }
    await loadProject(projectId);
    setShowSetup(false);
    setNotice("Project opened");
  }

  async function importRecording(localPath: string) {
    setBusy("Importing source");
    setError("");
    try {
      const asset = await requestJson<Asset>(
        apiUrl,
        `/api/v1/projects/${projectId}/assets/import`,
        {
          method: "POST",
          body: JSON.stringify({ local_path: localPath.trim() }),
        },
      );
      setAssets((current) => [
        ...current.filter((item) => item.id !== asset.id),
        asset,
      ]);
      setAssetId(asset.id);
      setSourcePath("");
      setNotice("Recording imported and probed");
      void preparePlayback(projectId, asset.id);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Import failed");
    } finally {
      setBusy("");
    }
  }

  function addManualClip(event: FormEvent) {
    event.preventDefault();
    const start = parseTimestamp(manualDraft.start);
    const end = parseTimestamp(manualDraft.end);
    if (start === null || end === null || end <= start) {
      setError("Enter a valid In and Out time; Out must be after In");
      return;
    }
    if (end > sourceDuration) {
      setError("The manual clip cannot end after the recording");
      return;
    }
    if (
      manualClips.some(
        (clip) => start < clip.end && end > clip.start,
      )
    ) {
      setError("Manual clips cannot overlap");
      return;
    }
    setError("");
    setManualClips((current) =>
      [
        ...current,
        {
          id: crypto.randomUUID(),
          title: manualDraft.title.trim() || `Highlight ${current.length + 1}`,
          start,
          end,
        },
      ].sort((left, right) => left.start - right.start),
    );
    setManualDraft({
      title: `Highlight ${manualClips.length + 2}`,
      start: "",
      end: "",
    });
    setNotice("Manual clip added to the first cut");
  }

  async function loadCreatedPlan(
    result: {
      plan: Plan;
      review_session: ReviewSnapshot["session"];
    },
    completeCopy: (count: number) => string,
  ) {
    setPlan(result.plan);
    const snapshot = await requestJson<ReviewSnapshot>(
      apiUrl,
      `/api/v1/projects/${projectId}/gaming/highlight-reviews/${result.review_session.id}`,
    );
    setReview(snapshot);
    const firstCandidate = snapshot.session.candidates[0];
    setSelectedId(firstCandidate?.id ?? "");
    if (firstCandidate) {
      setBoundaryDraft({
        start: formatTimestamp(firstCandidate.start_seconds),
        end: formatTimestamp(firstCandidate.end_seconds),
      });
      setBoundaryWindow(
        createBoundaryWindow(
          firstCandidate.start_seconds,
          firstCandidate.end_seconds,
          activeAsset?.duration_seconds ?? firstCandidate.end_seconds + 30,
        ),
      );
      setPlayheadSeconds(firstCandidate.start_seconds);
    }
    const human = await requestJson<HumanReview>(
      apiUrl,
      `/api/v1/projects/${projectId}/gaming/highlight-reviews/${result.review_session.id}/human-review`,
    );
    setHumanReview(human);
    setNotice(completeCopy(snapshot.metrics.candidate_count));
    await renderPreview(result.plan.id);
  }

  async function createManualFirstCut() {
    if (!manualClips.length) {
      setError("Add at least one manual clip first");
      return;
    }
    setDemoApproved(false);
    setTrimDrafts({});
    setTrimHistory({});
    setManualSavedTrims({});
    setPlanRevisionNeeded(false);
    setPreviewJob(null);
    setPreviewJobId("");
    setFinalJob(null);
    if (mode === "demo") {
      setDemoCandidates(
        manualClips.map((clip, index) => ({
          id: `demo-manual-${clip.id}`,
          index,
          title: clip.title,
          start: clip.start,
          end: clip.end,
          score: 1,
          signalIds: [`demo-manual-marker-${index + 1}`],
          labels: ["manual selection"],
          decision: "pending",
        })),
      );
      setSelectedId(`demo-manual-${manualClips[0].id}`);
      setNotice(`Manual first cut ready · ${manualClips.length} clips`);
      return;
    }
    if (!projectId || !assetId) {
      setError("Choose a project and recording first");
      return;
    }
    setBusy("Creating manual first cut");
    setError("");
    try {
      const result = await requestJson<{
        plan: Plan;
        review_session: ReviewSnapshot["session"];
      }>(
        apiUrl,
        `/api/v1/projects/${projectId}/assets/${assetId}/gaming/manual-highlight-plans`,
        {
          method: "POST",
          body: JSON.stringify({
            brief: {
              objective: "Keep the manually selected clips",
              editing_profile: "gameplay_highlights",
              aspect_ratio: "16:9",
              style: "Exact human-selected gaming highlights",
            },
            clips: manualClips.map((clip) => ({
              title: clip.title,
              start_seconds: clip.start,
              end_seconds: clip.end,
            })),
          }),
        },
      );
      await loadCreatedPlan(
        result,
        (count) => `Manual first cut ready · ${count} clips`,
      );
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Manual first cut failed",
      );
    } finally {
      setBusy("");
    }
  }

  async function runAnalysis() {
    if (mode === "demo") {
      setDemoApproved(false);
      setTrimDrafts({});
      setTrimHistory({});
      setManualSavedTrims({});
      setPlanRevisionNeeded(false);
      setPreviewJob(null);
      setPreviewJobId("");
      setFinalJob(null);
      setBusy("Analysing recording");
      setTimeout(() => {
        setBusy("");
        setNotice("Analysis complete · 4 evidence-backed clips proposed");
      }, 700);
      return;
    }
    if (!projectId || !assetId) {
      setError("Choose a project and recording first");
      return;
    }
    if (!automationReady) {
      setError(
        "Kill & teamfight detection needs Tesseract OCR. Use Manual mode until it is available.",
      );
      return;
    }
    setBusy("Analysing recording");
    setError("");
    setTrimDrafts({});
    setTrimHistory({});
    setManualSavedTrims({});
    setPlanRevisionNeeded(false);
    setPreviewJob(null);
    setPreviewJobId("");
    setFinalJob(null);
    try {
      const result = await requestJson<{
        plan: Plan;
        review_session: ReviewSnapshot["session"];
      }>(
        apiUrl,
        `/api/v1/projects/${projectId}/assets/${assetId}/gaming/auto-highlight-plans`,
        {
          method: "POST",
          body: JSON.stringify({
            brief: {
              objective: "Detect League kills and combine nearby kills into teamfight clips",
              editing_profile: "gameplay_highlights",
              target_duration_seconds: targetDuration,
              aspect_ratio: "16:9",
              style: "Kill and teamfight clips",
              additional_instructions:
                "Keep setup before each detected fight and combine nearby kills. Treat the duration as a maximum; a shorter valid edit is acceptable.",
            },
            game_id: "league_of_legends",
            max_highlights: maxHighlights,
          }),
        },
      );
      await loadCreatedPlan(
        result,
        (count) => `Automation complete · ${count} clips proposed`,
      );
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "Analysis failed",
      );
    } finally {
      setBusy("");
    }
  }

  async function renderPreview(planId: string) {
    if (mode === "demo") {
      setNotice("Preview render ready");
      return;
    }
    setNotice("Preview rendering");
    const job = await requestJson<RenderJob>(
      apiUrl,
      `/api/v1/projects/${projectId}/edit-plans/${planId}/renders`,
      {
        method: "POST",
        body: JSON.stringify({
          preset: {
            profile: "preview",
            aspect_ratio: "16:9",
            frames_per_second: 30,
            transition_duration_seconds: 0.25,
            audio_output_mode: "source_mix",
          },
        }),
      },
    );
    setPreviewJob(job);
    const completed = await waitForJob(job.id);
    setPreviewJob(completed);
    if (completed.status === "succeeded") {
      setPreviewJobId(job.id);
      setNotice("Preview render ready");
      return;
    }
    throw new Error(completed.error ?? "Preview render failed");
  }

  async function waitForJob(jobId: string): Promise<RenderJob> {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const job = await requestJson<RenderJob>(
        apiUrl,
        `/api/v1/jobs/${jobId}`,
      );
      if (job.status === "succeeded" || job.status === "failed") return job;
      await new Promise((resolve) => window.setTimeout(resolve, 750));
    }
    throw new Error("Render timed out before reaching a terminal state");
  }

  const decide = useCallback(
    async (candidate: Candidate, decision: Decision) => {
      if (mode === "demo") {
        setDemoCandidates((current) =>
          current.map((item) =>
            item.id === candidate.id
              ? {
                  ...item,
                  start: candidate.start,
                  end: candidate.end,
                  decision,
                }
              : item,
          ),
        );
        setTrimDrafts((current) => {
          const next = { ...current };
          delete next[candidate.id];
          return next;
        });
        setDemoApproved(false);
        setPlanRevisionNeeded(true);
        setNotice(`${candidate.title} marked ${statusLabel(decision)}`);
        return;
      }
      if (!review) return;
      if (candidate.id.startsWith("manual-")) {
        if (decision !== "adjust") return;
        setManualSavedTrims((current) => ({
          ...current,
          [candidate.id]: {
            start: candidate.start,
            end: candidate.end,
          },
        }));
        setTrimDrafts((current) => {
          const next = { ...current };
          delete next[candidate.id];
          return next;
        });
        setPlanRevisionNeeded(true);
        setNotice(`${candidate.title} cut saved`);
        return;
      }
      setBusy("Saving decision");
      setError("");
      try {
        const body =
          decision === "adjust"
            ? {
                action: "adjust",
                candidate_id: candidate.id,
                start_seconds: candidate.start,
                end_seconds: candidate.end,
                note: "Adjusted in the human review console",
              }
            : { action: decision, candidate_id: candidate.id };
        const snapshot = await requestJson<ReviewSnapshot>(
          apiUrl,
          `/api/v1/projects/${projectId}/gaming/highlight-reviews/${review.session.id}/decisions`,
          { method: "POST", body: JSON.stringify(body) },
        );
        setReview(snapshot);
        setTrimDrafts((current) => {
          const next = { ...current };
          delete next[candidate.id];
          return next;
        });
        setPlanRevisionNeeded(true);
        setNotice(`${candidate.title} marked ${statusLabel(decision)}`);
      } catch (nextError) {
        setError(nextError instanceof Error ? nextError.message : "Save failed");
      } finally {
        setBusy("");
      }
    },
    [apiUrl, mode, projectId, review],
  );

  async function addMissedHighlight(event: FormEvent) {
    event.preventDefault();
    const start = parseTimestamp(missedDraft.start);
    const end = parseTimestamp(missedDraft.end);
    if (
      start === null ||
      end === null ||
      end <= start ||
      end > sourceDuration
    ) {
      setError(
        `Use a valid MM:SS.s range inside the ${formatTimestamp(sourceDuration)} source`,
      );
      return;
    }
    const title = missedDraft.event.trim() || "Missed highlight";
    if (mode === "demo") {
      const manual: Candidate = {
        id: `manual-demo-${Date.now()}`,
        index: demoCandidates.length,
        title: `Manual · ${title}`,
        start,
        end,
        score: 1,
        signalIds: [],
        labels: ["manual review"],
        decision: "accept",
      };
      setDemoCandidates((current) => [...current, manual]);
      setSelectedId(manual.id);
      setPlanRevisionNeeded(true);
      setDemoApproved(false);
      setNotice("Missed highlight added to the review");
      return;
    }
    if (!review) {
      setError("Run analysis before adding a missed highlight");
      return;
    }
    setBusy("Adding missed highlight");
    setError("");
    try {
      const snapshot = await requestJson<ReviewSnapshot>(
        apiUrl,
        `/api/v1/projects/${projectId}/gaming/highlight-reviews/${review.session.id}/decisions`,
        {
          method: "POST",
          body: JSON.stringify({
            action: "missed_highlight",
            start_seconds: start,
            end_seconds: end,
            event_name: title,
            note: "Added in the human review console",
          }),
        },
      );
      setReview(snapshot);
      const decision = [...snapshot.decisions]
        .reverse()
        .find((item) => item.action === "missed_highlight");
      if (decision) setSelectedId(`manual-${decision.id}`);
      setPlanRevisionNeeded(true);
      setNotice("Missed highlight added · save a new plan version");
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Missed highlight could not be added",
      );
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        target?.matches("input, textarea, select, [contenteditable='true']")
      ) {
        return;
      }
      if (!selected) return;
      if (event.key.toLowerCase() === "a") {
        event.preventDefault();
        void decide(selected, "accept");
      }
      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        void decide(selected, "reject");
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [decide, selected]);

  function updateBoundary(field: "start" | "end", value: number) {
    if (!selected) return;
    setTrimDrafts((current) => {
      const draft = current[selected.id] ?? {
        start: selected.start,
        end: selected.end,
      };
      return {
        ...current,
        [selected.id]: { ...draft, [field]: value },
      };
    });
    if (mode === "demo") setDemoApproved(false);
  }

  function currentBaseTrim(candidate: Candidate): TrimPoint {
    if (mode === "demo") {
      const original = demoCandidates.find((item) => item.id === candidate.id);
      return {
        start: original?.start ?? candidate.start,
        end: original?.end ?? candidate.end,
      };
    }
    const sourceCandidate = review?.session.candidates.find(
      (item) => item.id === candidate.id,
    );
    const decision = review?.latest_candidate_decisions.find(
      (item) => item.candidate_id === candidate.id,
    );
    return {
      start:
        decision?.start_seconds ??
        sourceCandidate?.start_seconds ??
        candidate.start,
      end:
        decision?.end_seconds ?? sourceCandidate?.end_seconds ?? candidate.end,
    };
  }

  function recordTrimHistory(pointOverride?: TrimPoint) {
    if (!selected) return;
    const point = pointOverride ?? {
      start: selected.start,
      end: selected.end,
    };
    setTrimHistory((current) => {
      const existing = current[selected.id] ?? {
        entries: [currentBaseTrim(selected)],
        cursor: 0,
      };
      const currentPoint = existing.entries[existing.cursor];
      if (currentPoint && sameTrim(currentPoint, point)) return current;
      const entries = [...existing.entries.slice(0, existing.cursor + 1), point];
      return {
        ...current,
        [selected.id]: { entries, cursor: entries.length - 1 },
      };
    });
  }

  function restoreTrimHistory(cursor: number) {
    if (!selected || !activeTrimHistory) return;
    const boundedCursor = Math.max(
      0,
      Math.min(cursor, activeTrimHistory.entries.length - 1),
    );
    const point = activeTrimHistory.entries[boundedCursor];
    setTrimHistory((current) => ({
      ...current,
      [selected.id]: { ...activeTrimHistory, cursor: boundedCursor },
    }));
    setTrimDrafts((current) => {
      const next = { ...current };
      if (sameTrim(point, currentBaseTrim(selected))) {
        delete next[selected.id];
      } else {
        next[selected.id] = point;
      }
      return next;
    });
    setBoundaryDraft({
      start: formatTimestamp(point.start),
      end: formatTimestamp(point.end),
    });
    seekPreview(point.start);
    if (mode === "demo") setDemoApproved(false);
  }

  function resetTrim() {
    if (!selected) return;
    const point = currentBaseTrim(selected);
    setTrimDrafts((current) => {
      const next = { ...current };
      delete next[selected.id];
      return next;
    });
    setBoundaryDraft({
      start: formatTimestamp(point.start),
      end: formatTimestamp(point.end),
    });
    setBoundaryWindow(
      createBoundaryWindow(point.start, point.end, sourceDuration),
    );
    setTrimHistory((current) => ({
      ...current,
      [selected.id]: { entries: [point], cursor: 0 },
    }));
    seekPreview(point.start);
    if (mode === "demo") setDemoApproved(false);
  }

  function selectCandidate(candidate: Candidate) {
    setSelectedId(candidate.id);
    setBoundaryDraft({
      start: formatTimestamp(candidate.start),
      end: formatTimestamp(candidate.end),
    });
    setBoundaryError("");
    setBoundaryWindow(
      createBoundaryWindow(
        candidate.start,
        candidate.end,
        sourceDuration,
      ),
    );
    if (previewMode !== "reel") {
      window.requestAnimationFrame(() => seekPreview(candidate.start));
    }
    setRightOpen(true);
  }

  function seekPreview(seconds: number) {
    const safeSeconds = Math.max(0, Math.min(seconds, sourceDuration));
    setPlayheadSeconds(safeSeconds);
    if (!previewRef.current) return;
    previewRef.current.currentTime = Math.max(
      0,
      Math.min(safeSeconds, previewRef.current.duration || sourceDuration),
    );
  }

  function selectPreviewMode(nextMode: PreviewMode) {
    setPreviewMode(nextMode);
    window.requestAnimationFrame(() => {
      if (!previewRef.current || !selected) return;
      if (nextMode === "source" || nextMode === "cut") {
        seekPreview(selected.start);
      }
    });
  }

  function previewBoundary(field: "start" | "end") {
    if (!selected) return;
    setPreviewMode("cut");
    window.requestAnimationFrame(() => {
      seekPreview(field === "start" ? selected.start : Math.max(selected.start, selected.end - 0.2));
    });
  }

  function playSelectedCut() {
    if (!selected) return;
    setPreviewMode("cut");
    window.requestAnimationFrame(() => {
      if (!previewRef.current) return;
      seekPreview(selected.start);
      void previewRef.current.play();
    });
  }

  function changeBoundary(
    field: "start" | "end",
    value: number,
    commitHistory = false,
  ) {
    if (!selected) return;
    const nextValue = clampBoundary(
      field,
      value,
      selected.start,
      selected.end,
      sourceDuration,
    );
    updateBoundary(field, nextValue);
    setBoundaryDraft((current) => ({
      ...current,
      [field]: formatTimestamp(nextValue),
    }));
    setBoundaryError("");
    seekPreview(nextValue);
    if (commitHistory) {
      recordTrimHistory({
        start: field === "start" ? nextValue : selected.start,
        end: field === "end" ? nextValue : selected.end,
      });
    }
  }

  function commitBoundary(field: "start" | "end") {
    const parsed = parseTimestamp(boundaryDraft[field]);
    if (parsed === null) {
      setBoundaryError("Use MM:SS.s, for example 16:54.8");
      if (selected) {
        setBoundaryDraft((current) => ({
          ...current,
          [field]: formatTimestamp(selected[field]),
        }));
      }
      return;
    }
    changeBoundary(field, parsed, true);
  }

  function updateMaximumDuration(part: "minutes" | "seconds", value: number) {
    const safeValue = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
    const minutes = part === "minutes" ? Math.min(30, safeValue) : maximumMinutes;
    const seconds =
      part === "seconds" ? Math.min(59, safeValue) : maximumSeconds;
    setTargetDuration(Math.max(10, Math.min(1800, minutes * 60 + seconds)));
  }

  async function savePlanRevision() {
    if (hasUnsavedTrims) {
      setError("Save each cut adjustment before creating the plan revision");
      return;
    }
    if (!planRevisionNeeded) {
      setNotice("No review changes need a new plan version");
      return;
    }
    if (mode === "demo") {
      setDemoApproved(false);
      const nextVersion = demoPlanVersion + 1;
      setDemoPlanVersion(nextVersion);
      setPlanRevisionNeeded(false);
      setNotice(`Plan v${nextVersion} saved · approval required`);
      return;
    }
    if (!review || !humanReview || !plan) return;
    if (pendingCount) {
      setError("Resolve every candidate before saving a plan revision");
      return;
    }
    setBusy("Saving plan revision");
    setError("");
    try {
      const segments = candidates
        .filter((candidate) => candidate.decision !== "reject")
        .sort((left, right) => left.index - right.index)
        .map((candidate) => ({
          asset_id: review.session.asset_id,
          source_in_seconds: candidate.start,
          source_out_seconds: candidate.end,
          purpose: candidate.title,
          transcript_segment_ids: [],
          highlight_signal_ids: candidate.signalIds,
        }));
      const nextHuman = await requestJson<HumanReview>(
        apiUrl,
        `/api/v1/projects/${projectId}/gaming/highlight-reviews/${review.session.id}/human-review/revisions`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_plan_id: humanReview.state.current_plan_id,
            draft: {
              title: plan.title,
              summary: `${plan.summary} Human-reviewed in the gaming console.`,
              segments,
            },
          }),
        },
      );
      setHumanReview(nextHuman);
      setPlan(nextHuman.plan);
      setPlanRevisionNeeded(false);
      setNotice(`Plan v${nextHuman.plan.version} saved · approval required`);
      await renderPreview(nextHuman.plan.id);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "Revision failed",
      );
    } finally {
      setBusy("");
    }
  }

  async function startReviewer() {
    if (aiAssistance === "off") {
      setError("Turn AI assistance to Review before starting Agent 2");
      return;
    }
    if (!reviewerAvailable) {
      setError(
        "Review mode is saved, but a local reviewer model is not configured yet. Manual review and approval still work.",
      );
      return;
    }
    if (mode === "demo") {
      setBusy("Reviewer inspecting preview");
      setTimeout(() => {
        setBusy("");
        setWorkflow({
          id: "demo-workflow",
          state: "approved",
          reviewer_name: "simulated-independent-reviewer",
          current_plan_id: "demo-plan-v4",
          rounds: [
            {
              round_number: 1,
              verdict: {
                outcome: "approve",
                confidence: 0.91,
                summary:
                  "Strong event coverage, clean boundaries, and no duplicated action.",
                corrections: [],
              },
            },
          ],
        });
        setNotice("Agent 2 approved · human approval still required");
      }, 850);
      return;
    }
    if (!review) return;
    setBusy("Starting reviewer");
    setError("");
    try {
      const nextWorkflow = await requestJson<Workflow>(
        apiUrl,
        `/api/v1/projects/${projectId}/gaming/highlight-reviews/${review.session.id}/agent-workflows`,
        {
          method: "POST",
          body: JSON.stringify({
            maximum_review_rounds: 2,
            render_preset: {
              profile: "preview",
              aspect_ratio: "16:9",
              frames_per_second: 30,
              transition_duration_seconds: 0.25,
              audio_output_mode: "source_mix",
            },
          }),
        },
      );
      setWorkflow(nextWorkflow);
      setNotice("Agent 2 review started");
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "Reviewer could not start",
      );
    } finally {
      setBusy("");
    }
  }

  async function approvePlan() {
    if (pendingCount) {
      setError("Resolve every proposed clip before human approval");
      return;
    }
    if (hasUnsavedTrims || planRevisionNeeded) {
      setError("Save cut decisions and create the new plan version before approval");
      return;
    }
    if (mode === "demo") {
      setError("");
      setDemoApproved(true);
      setNotice(`Human approval recorded for plan v${demoPlanVersion}`);
      return;
    }
    if (!review || !humanReview) return;
    setBusy("Recording approval");
    setError("");
    try {
      const nextHuman = await requestJson<HumanReview>(
        apiUrl,
        `/api/v1/projects/${projectId}/gaming/highlight-reviews/${review.session.id}/human-review/approval`,
        {
          method: "POST",
          body: JSON.stringify({
            plan_id: humanReview.plan.id,
            plan_version: humanReview.plan.version,
          }),
        },
      );
      setHumanReview(nextHuman);
      setPlan(nextHuman.plan);
      setNotice(`Human approval recorded for plan v${nextHuman.plan.version}`);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : "Approval failed",
      );
    } finally {
      setBusy("");
    }
  }

  async function renderFinal() {
    if (!isApproved || hasUnsavedTrims || planRevisionNeeded) {
      setError("Approve the current plan before requesting a final render");
      return;
    }
    if (mode === "demo") {
      setFinalJob({ id: "demo-final", status: "succeeded" });
      setNotice("Final render queued · no upload will occur");
      return;
    }
    if (!plan) return;
    setBusy("Queueing final render");
    setError("");
    try {
      const job = await requestJson<RenderJob>(
        apiUrl,
        `/api/v1/projects/${projectId}/edit-plans/${plan.id}/renders`,
        {
          method: "POST",
          body: JSON.stringify({
            preset: {
              profile: "final",
              aspect_ratio: "16:9",
              frames_per_second: 60,
              transition_duration_seconds: 0.25,
              audio_output_mode: "source_mix",
            },
          }),
        },
      );
      setFinalJob(job);
      setNotice("Final render queued · publishing is disabled");
      void waitForJob(job.id)
        .then((completed) => {
          setFinalJob(completed);
          setNotice(
            completed.status === "succeeded"
              ? "Final render ready · publishing is disabled"
              : completed.error ?? "Final render failed",
          );
        })
        .catch((nextError: unknown) =>
          setError(
            nextError instanceof Error
              ? nextError.message
              : "Final render status could not be refreshed",
          ),
        );
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Render failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <button
            className="mobile-panel-button"
            onClick={() => setLeftOpen((value) => !value)}
            aria-label="Toggle project panel"
            aria-expanded={leftOpen}
          >
            Menu
          </button>
          <div className="brand-mark" aria-hidden="true">
            VE
          </div>
          <div>
            <strong>Cutroom</strong>
            <span>Gaming workflow</span>
          </div>
        </div>
        <nav className="workflow-steps" aria-label="Workflow progress">
          {workflowSteps.map((step, index) => (
              <span
                key={step.label}
                className={`${step.complete ? "complete" : ""} ${
                  step.current ? "current" : ""
                }`}
              >
                <b>{index + 1}</b>
                {step.label}
              </span>
            ))}
        </nav>
        <div className="topbar-actions">
          <span className={`connection-dot ${connection}`} aria-hidden="true" />
          <span className="connection-label">
            {mode === "demo"
              ? "Demo data"
              : connection === "connected"
                ? "Local backend"
                : "Disconnected"}
          </span>
          <button
            className="mobile-panel-button"
            onClick={() => setRightOpen((value) => !value)}
            aria-label="Toggle evidence inspector"
            aria-expanded={rightOpen}
          >
            Inspect
          </button>
        </div>
      </header>

      {setupNeeded && (
        <section className="setup-screen" aria-labelledby="setup-title">
          <div className="setup-orbit setup-orbit-one" aria-hidden="true" />
          <div className="setup-orbit setup-orbit-two" aria-hidden="true" />
          <div className="setup-card">
            <div className="setup-intro">
              <span className="eyebrow">Welcome to Cutroom</span>
              <h1 id="setup-title">Where would you like to begin?</h1>
              <p>
                Start a fresh clips project or reopen a video folder you have
                used before. Recordings are added after you enter the editor.
              </p>
            </div>
            <div className="setup-choices">
              <form
                className="setup-choice-card create"
                onSubmit={createNewProject}
              >
                <span className="setup-choice-icon" aria-hidden="true">
                  +
                </span>
                <div>
                  <span className="setup-choice-kicker">Start fresh</span>
                  <h2>Create new project</h2>
                  <p>
                    Cutroom creates a protected workspace for previews and
                    finished clips. Add your first recording inside.
                  </p>
                </div>
                <label>
                  Project name
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder="League highlights"
                  />
                </label>
                <button
                  className="primary-button"
                  disabled={!projectName.trim() || Boolean(busy)}
                >
                  {busy === "Creating project"
                    ? "Creating…"
                    : "Create & enter"}
                </button>
              </form>

              <form
                className="setup-choice-card existing"
                onSubmit={
                  desktopMode
                    ? (event) => {
                        event.preventDefault();
                        void openExistingFolder();
                      }
                    : openExistingWebProject
                }
              >
                <span className="setup-choice-icon" aria-hidden="true">
                  ↗
                </span>
                <div>
                  <span className="setup-choice-kicker">Continue editing</span>
                  <h2>Open existing video folder</h2>
                  <p>
                    Choose its folder in Finder. Cutroom will reopen the linked
                    project or prepare that folder for editing.
                  </p>
                </div>
                {!desktopMode && (
                  <label>
                    Existing project
                    <select
                      value={projectId}
                      onChange={(event) => setProjectId(event.target.value)}
                    >
                      <option value="">Choose a project</option>
                      {projects.map((project) => (
                        <option value={project.id} key={project.id}>
                          {project.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  className="secondary-button"
                  disabled={
                    Boolean(busy) || (!desktopMode && !projectId)
                  }
                >
                  {busy === "Loading project"
                    ? "Opening…"
                    : desktopMode
                      ? "Choose folder in Finder"
                      : "Open project"}
                </button>
              </form>
            </div>
            <p className="setup-footnote">
              Original recordings are never changed or deleted.
            </p>
            {error && (
              <div className="setup-error" role="alert">
                {error}
                <button type="button" onClick={() => setError("")}>
                  Dismiss
                </button>
              </div>
            )}
          </div>
        </section>
      )}

      <div className={`workspace ${setupNeeded ? "setup-hidden" : ""}`}>
        <aside className={`left-rail ${leftOpen ? "open" : ""}`}>
          <section className="rail-section">
            <div className="section-heading">
              <span>Workspace</span>
              {!desktopMode && (
                <button
                  className="text-button"
                  onClick={() => {
                    setMode(mode === "demo" ? "live" : "demo");
                    setLeftOpen(false);
                  }}
                >
                  {mode === "demo" ? "Use backend" : "Use demo"}
                </button>
              )}
            </div>

            {mode === "live" ? (
              !desktopMode && (
                <div className="connection-panel">
                <label htmlFor="api-url">Backend address</label>
                <div className="inline-field">
                  <input
                    id="api-url"
                    value={apiUrl}
                    onChange={(event) => setApiUrl(event.target.value)}
                    spellCheck={false}
                  />
                  <button
                    onClick={() => void connect()}
                    disabled={connection === "checking"}
                  >
                    {connection === "checking" ? "…" : "Connect"}
                  </button>
                </div>
              </div>
              )
            ) : (
              <button
                className="project-card selected"
                onClick={() => setNotice("Demo project selected")}
              >
                <span className="project-thumb" aria-hidden="true">
                  32:18
                </span>
                <span>
                  <strong>Ranked session 24</strong>
                  <small>League of Legends · Today</small>
                </span>
              </button>
            )}

            {mode === "live" && connection === "connected" && (
              <button
                className="project-card selected"
                onClick={() => setShowSetup(true)}
              >
                <span className="project-thumb" aria-hidden="true">
                  {activeAsset ? formatTime(activeAsset.duration_seconds) : "NEW"}
                </span>
                <span>
                  <strong>
                    {projects.find((project) => project.id === projectId)?.name ??
                      "Clips project"}
                  </strong>
                  <small>Switch or create project</small>
                </span>
              </button>
            )}
          </section>

          <section className="rail-section source-section">
            <div className="section-heading">
              <span>Recordings</span>
              <span className="count">{mode === "demo" ? 1 : assets.length}</span>
            </div>
            {mode === "demo" ? (
              <button className="source-row selected">
                <span className="media-icon" aria-hidden="true">
                  MKV
                </span>
                <span>
                  <strong>ranked-session-24.mkv</strong>
                  <small>32:18 · 1440p · 60 fps</small>
                </span>
              </button>
            ) : (
              <>
                <button
                  className="add-recording-button"
                  onClick={() => void chooseSource()}
                  disabled={Boolean(busy)}
                >
                  <span aria-hidden="true">+</span>
                  <span>
                    <strong>
                      {assets.length ? "Add another recording" : "Add recording"}
                    </strong>
                    <small>MKV, MP4, MOV, AVI, or WebM</small>
                  </span>
                </button>
                {!desktopMode && (
                  <form
                    className="path-import"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (sourcePath.trim()) {
                        void importRecording(sourcePath);
                      }
                    }}
                  >
                    <input
                      aria-label="Recording path"
                      placeholder="/Users/you/Movies/game.mkv"
                      value={sourcePath}
                      onChange={(event) => setSourcePath(event.target.value)}
                    />
                    <button disabled={!sourcePath.trim() || Boolean(busy)}>
                      Import
                    </button>
                  </form>
                )}
                {!assets.length && (
                  <div className="empty-source">
                    <span aria-hidden="true">VIDEO</span>
                    <strong>Upload a recording to begin</strong>
                    <small>
                      Choose an existing clip or add a full gameplay recording.
                    </small>
                  </div>
                )}
                {assets.map((asset) => (
                  <button
                    key={asset.id}
                    className={`source-row ${assetId === asset.id ? "selected" : ""}`}
                    onClick={() => {
                      void loadProject(projectId, asset.id);
                      setLeftOpen(false);
                    }}
                  >
                    <span className="media-icon" aria-hidden="true">
                      {asset.source_path.split(".").at(-1)?.toUpperCase()}
                    </span>
                    <span>
                      <strong>{asset.source_path.split("/").at(-1)}</strong>
                      <small>
                        {formatTime(asset.duration_seconds)} · {asset.width}×
                        {asset.height} · {Math.round(asset.frame_rate)} fps
                      </small>
                      {assetId === asset.id && playback && (
                        <small className={`playback-state ${playback.status}`}>
                          {playbackStatusCopy(playback)}
                        </small>
                      )}
                    </span>
                  </button>
                ))}
              </>
            )}
          </section>

          <section className="rail-section status-section">
            <div className="section-heading">
              <span>Run status</span>
            </div>
            <ol className="run-list">
              <li className={sourceReady ? "done" : "active"}>
                <span />
                <div>
                  <strong>Source registered</strong>
                  <small>
                    {sourceReady
                      ? playback
                        ? playbackStatusCopy(playback)
                        : "Path and duration verified"
                      : "Choose a recording"}
                  </small>
                </div>
              </li>
              <li className={candidates.length ? "done" : ""}>
                <span />
                <div>
                  <strong>
                    {editWorkflow === "automation"
                      ? "Evidence analysed"
                      : "Manual first cut"}
                  </strong>
                  <small>
                    {editWorkflow === "automation"
                      ? "Kill OCR + teamfight merging"
                      : "Exact ranges chosen by you"}
                  </small>
                </div>
              </li>
              <li
                className={
                  !reviewerAvailable
                    ? "optional"
                    : workflow?.state === "approved"
                      ? "done"
                      : analysisReady
                        ? "active"
                        : ""
                }
              >
                <span />
                <div>
                  <strong>Independent review · optional</strong>
                  <small>
                    {aiAssistance === "off"
                      ? "AI assistance off"
                      : !reviewerConfigured
                        ? "Local reviewer not configured"
                      : workflow
                        ? statusLabel(workflow.state)
                        : "Ready after preview"}
                  </small>
                </div>
              </li>
              <li className={reviewReady ? "done" : analysisReady ? "active" : ""}>
                <span />
                <div>
                  <strong>Human approval</strong>
                  <small>
                    {hasUnsavedTrims
                      ? "Unsaved trim changes"
                      : planRevisionNeeded
                        ? "New plan version required"
                        : isApproved
                          ? "Current plan approved"
                          : "Required"}
                  </small>
                </div>
              </li>
              <li className={exportReady ? "done" : reviewReady ? "active" : ""}>
                <span />
                <div>
                  <strong>Final output</strong>
                  <small>
                    {finalJob?.status
                      ? statusLabel(finalJob.status)
                      : "Not rendered"}
                  </small>
                </div>
              </li>
            </ol>
          </section>

          {mode === "live" && diagnostics && (
            <section className="rail-section diagnostics-section">
              <div className="section-heading">
                <span>System check</span>
                <span className={`diagnostic-summary ${diagnostics.status}`}>
                  {diagnostics.status}
                </span>
              </div>
              <ul className="diagnostic-list">
                {[
                  {
                    label: "Database",
                    status: diagnostics.database,
                    detail: "Project history",
                  },
                  {
                    label: "Workspace",
                    status: diagnostics.workspace,
                    detail: formatStorage(diagnostics.workspace_free_bytes),
                  },
                  {
                    label: diagnostics.ffmpeg.name,
                    status: diagnostics.ffmpeg.status,
                    detail: diagnostics.ffmpeg.version ?? "Required for rendering",
                  },
                  {
                    label: diagnostics.ffprobe.name,
                    status: diagnostics.ffprobe.status,
                    detail: diagnostics.ffprobe.version ?? "Required for imports",
                  },
                  {
                    label: diagnostics.tesseract.name,
                    status: diagnostics.tesseract.status,
                    detail:
                      diagnostics.tesseract.version ??
                      "Needed for Kill & teamfight detection",
                  },
                ].map((item) => (
                  <li key={item.label}>
                    <span className={item.status} aria-hidden="true" />
                    <div>
                      <strong>{item.label}</strong>
                      <small>{item.detail}</small>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </aside>

        <section className="editor">
          <div className="editor-header">
            <div>
              <span className="eyebrow">League of Legends · Gameplay highlights</span>
              <h1>
                {plan?.title ??
                  (mode === "live"
                    ? projects.find((project) => project.id === projectId)
                        ?.name ?? "New clips project"
                    : "Ranked session 24")}
              </h1>
            </div>
            <div className="plan-meta">
              <span>
                PLAN v
                {mode === "demo"
                  ? demoPlanVersion
                  : (humanReview?.plan.version ?? plan?.version ?? 1)}
              </span>
              <span
                className={
                  hasUnsavedTrims || planRevisionNeeded
                    ? "unsaved"
                    : isApproved
                      ? "approved"
                      : "unapproved"
                }
              >
                {hasUnsavedTrims
                  ? "Unsaved trim"
                  : planRevisionNeeded
                    ? "Revision needed"
                    : isApproved
                      ? "Human approved"
                      : "Approval required"}
              </span>
            </div>
          </div>

          {mode === "live" && !activeAsset && (
            <section className="empty-project-callout" aria-labelledby="empty-project-title">
              <span className="empty-project-icon" aria-hidden="true">
                ▶
              </span>
              <div>
                <span className="eyebrow">Project ready</span>
                <h2 id="empty-project-title">Upload a recording to begin</h2>
                <p>
                  Choose an existing video clip or a full gameplay recording.
                  Your original file stays untouched.
                </p>
              </div>
              <button
                className="primary-button"
                onClick={() => void chooseSource()}
                disabled={Boolean(busy)}
              >
                {busy === "Importing source" ? "Importing…" : "Choose recording"}
              </button>
            </section>
          )}

          <section className="workflow-panel" aria-labelledby="workflow-mode-title">
            <div className="workflow-panel-heading">
              <div>
                <span className="eyebrow">Workflow setup</span>
                <h2 id="workflow-mode-title">Choose how the first cut is made</h2>
              </div>
              <span className="saved-setting">Saved on this Mac</span>
            </div>
            <div className="workflow-choice-grid">
              <button
                className={editWorkflow === "manual" ? "selected" : ""}
                onClick={() => setEditWorkflow("manual")}
                aria-pressed={editWorkflow === "manual"}
              >
                <strong>Manual</strong>
                <span>You choose exact In and Out times. No detector or AI needed.</span>
              </button>
              <button
                className={editWorkflow === "automation" ? "selected" : ""}
                onClick={() => setEditWorkflow("automation")}
                aria-pressed={editWorkflow === "automation"}
              >
                <strong>Kill &amp; teamfight detection</strong>
                <span>Find visible League kills and merge nearby kills. No LLM.</span>
              </button>
            </div>
            <div className="ai-setting">
              <div>
                <strong>AI assistance</strong>
                <small>
                  Separate from kill detection. Rendering and detection never send a text prompt.
                </small>
              </div>
              <div className="ai-options" role="group" aria-label="AI assistance">
                <button
                  className={aiAssistance === "off" ? "selected" : ""}
                  onClick={() => setAiAssistance("off")}
                  aria-pressed={aiAssistance === "off"}
                >
                  Off
                </button>
                <button
                  className={aiAssistance === "review" ? "selected" : ""}
                  onClick={() => setAiAssistance("review")}
                  aria-pressed={aiAssistance === "review"}
                >
                  Review
                </button>
                <button disabled title="Full local-model editing will be added later">
                  Full · later
                </button>
              </div>
              <small className="ai-setting-status">
                {aiAssistance === "off"
                  ? "No model will be called."
                  : reviewerConfigured
                    ? "Independent AI review is ready after preview rendering."
                    : "Review mode is saved, but no local reviewer model is configured yet."}
              </small>
            </div>
          </section>

          <section className="brief-panel" aria-labelledby="brief-title">
            <div className="brief-heading">
              <div>
                <span className="eyebrow">
                  {editWorkflow === "automation"
                    ? "Deterministic automation"
                    : "Manual first cut"}
                </span>
                <h2 id="brief-title">
                  {editWorkflow === "automation"
                    ? "Kill & teamfight detection"
                    : "Add the exact moments you want to keep"}
                </h2>
              </div>
              <button
                className="primary-button"
                onClick={
                  editWorkflow === "automation"
                    ? runAnalysis
                    : createManualFirstCut
                }
                disabled={
                  Boolean(busy) ||
                  !sourceReady ||
                  (editWorkflow === "manual" && manualClips.length === 0) ||
                  (editWorkflow === "automation" && !automationReady)
                }
              >
                {editWorkflow === "automation"
                  ? busy === "Analysing recording"
                    ? "Analysing…"
                    : "Detect kills & teamfights"
                  : busy === "Creating manual first cut"
                    ? "Creating…"
                    : "Create manual first cut"}
              </button>
            </div>
            {editWorkflow === "automation" ? (
              <>
                <div className="detector-summary">
                  <div>
                    <span className="detector-icon">K</span>
                    <strong>Champion kills</strong>
                    <small>Reads visible League kill announcements</small>
                  </div>
                  <div>
                    <span className="detector-icon">×2</span>
                    <strong>Multi-kills</strong>
                    <small>Prioritises double, triple, quadra, and penta kills</small>
                  </div>
                  <div>
                    <span className="detector-icon">TF</span>
                    <strong>Teamfights</strong>
                    <small>Nearby kills are combined into one continuous clip</small>
                  </div>
                </div>
                {!automationReady && (
                  <div className="automation-requirement" role="status">
                    <strong>Kill detection needs Tesseract OCR.</strong>
                    <span>
                      Manual mode is available now. Batch 4 can bundle this
                      detector so viewers do not install it separately.
                    </span>
                  </div>
                )}
                <div className="brief-controls">
                  <div className="duration-control">
                    <span className="control-label">Max duration</span>
                    <div className="duration-inputs">
                      <label>
                        <span className="sr-only">Maximum minutes</span>
                        <input
                          type="number"
                          min={0}
                          max={30}
                          value={maximumMinutes}
                          onChange={(event) =>
                            updateMaximumDuration(
                              "minutes",
                              Number(event.target.value),
                            )
                          }
                          aria-label="Maximum minutes"
                        />
                        <small>min</small>
                      </label>
                      <span className="duration-separator">:</span>
                      <label>
                        <span className="sr-only">Maximum seconds</span>
                        <input
                          type="number"
                          min={0}
                          max={59}
                          value={maximumSeconds}
                          onChange={(event) =>
                            updateMaximumDuration(
                              "seconds",
                              Number(event.target.value),
                            )
                          }
                          aria-label="Maximum seconds"
                        />
                        <small>sec</small>
                      </label>
                    </div>
                    <small className="duration-help">
                      Upper limit · shorter valid reels still continue
                    </small>
                  </div>
                  <label>
                    Max clips
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={maxHighlights}
                      onChange={(event) =>
                        setMaxHighlights(Number(event.target.value))
                      }
                    />
                  </label>
                  <label>
                    Format
                    <select defaultValue="16:9">
                      <option>16:9</option>
                      <option disabled>9:16 · later milestone</option>
                    </select>
                  </label>
                  <span className="safety-note">
                    Publishing and source deletion are disabled
                  </span>
                </div>
              </>
            ) : (
              <>
                <form className="manual-clip-form" onSubmit={addManualClip}>
                  <label>
                    Clip name
                    <input
                      value={manualDraft.title}
                      onChange={(event) =>
                        setManualDraft((current) => ({
                          ...current,
                          title: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label>
                    In <small>MM:SS.s</small>
                    <input
                      value={manualDraft.start}
                      placeholder="00:10.0"
                      onChange={(event) =>
                        setManualDraft((current) => ({
                          ...current,
                          start: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <button
                    type="button"
                    className="text-button manual-playhead"
                    onClick={() =>
                      setManualDraft((current) => ({
                        ...current,
                        start: formatTimestamp(playheadSeconds),
                      }))
                    }
                  >
                    Use playhead
                  </button>
                  <label>
                    Out <small>MM:SS.s</small>
                    <input
                      value={manualDraft.end}
                      placeholder="00:25.0"
                      onChange={(event) =>
                        setManualDraft((current) => ({
                          ...current,
                          end: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <button
                    type="button"
                    className="text-button manual-playhead"
                    onClick={() =>
                      setManualDraft((current) => ({
                        ...current,
                        end: formatTimestamp(playheadSeconds),
                      }))
                    }
                  >
                    Use playhead
                  </button>
                  <button className="secondary-button">Add clip</button>
                </form>
                <div className="manual-clip-list" aria-live="polite">
                  {manualClips.length ? (
                    manualClips.map((clip, index) => (
                      <div key={clip.id}>
                        <span>{index + 1}</span>
                        <strong>{clip.title}</strong>
                        <small>
                          {formatTimestamp(clip.start)}–{formatTimestamp(clip.end)}
                        </small>
                        <button
                          className="text-button"
                          onClick={() =>
                            setManualClips((current) =>
                              current.filter((item) => item.id !== clip.id),
                            )
                          }
                        >
                          Remove
                        </button>
                      </div>
                    ))
                  ) : (
                    <p>
                      Play the source below, add one or more ranges, then create
                      the first cut. Manual mode does not need Tesseract or AI.
                    </p>
                  )}
                </div>
              </>
            )}
          </section>

          <section className="preview-workspace" aria-label="Video preview">
            <div className="preview-toolbar">
              <div>
                <span className="eyebrow">Preview</span>
                <strong>
                  {previewMode === "source"
                    ? "Source overview"
                    : previewMode === "cut"
                      ? "Selected cut"
                      : "Generated reel"}
                </strong>
                <small>
                  {previewMode === "source"
                    ? "Inspect the full recording around every detection."
                    : previewMode === "cut"
                      ? "Playback is limited to the current In and Out points."
                      : "Watch the combined result after rendering."}
                </small>
              </div>
              <div className="preview-switch" role="group" aria-label="Preview mode">
                <button
                  className={previewMode === "source" ? "active" : ""}
                  onClick={() => selectPreviewMode("source")}
                >
                  Source
                </button>
                <button
                  className={previewMode === "cut" ? "active" : ""}
                  onClick={() => selectPreviewMode("cut")}
                >
                  Current cut
                </button>
                <button
                  className={previewMode === "reel" ? "active" : ""}
                  onClick={() => selectPreviewMode("reel")}
                  disabled={mode === "live" && !previewJobId}
                >
                  Generated reel
                </button>
              </div>
            </div>

            <div className="viewer">
              {mode === "live" && activeMediaUrl ? (
                <video
                  key={`${previewMode}-${activeMediaUrl}`}
                  ref={previewRef}
                  controls
                  preload="metadata"
                  src={activeMediaUrl}
                  onLoadedMetadata={() => {
                    if (previewMode !== "reel" && selected) {
                      seekPreview(selected.start);
                    }
                  }}
                  onPlay={() => {
                    if (
                      previewMode === "cut" &&
                      selected &&
                      previewRef.current &&
                      (previewRef.current.currentTime < selected.start ||
                        previewRef.current.currentTime >= selected.end)
                    ) {
                      seekPreview(selected.start);
                    }
                  }}
                  onTimeUpdate={() => {
                    if (previewRef.current) {
                      setPlayheadSeconds(previewRef.current.currentTime);
                    }
                    if (
                      previewMode === "cut" &&
                      selected &&
                      previewRef.current &&
                      previewRef.current.currentTime >= selected.end
                    ) {
                      previewRef.current.pause();
                      seekPreview(selected.start);
                    }
                  }}
                  onError={() =>
                    setError(
                      previewMode === "reel"
                        ? "The rendered reel is not available for playback"
                        : "The browser preview could not be played. Retry preparation or inspect the backend status.",
                    )
                  }
                >
                  Your browser cannot play this media format. MP4 is recommended
                  for browser preview.
                </video>
              ) : mode === "live" ? (
                <div className="empty-viewer">
                  <span>NO MEDIA</span>
                  <strong>
                    {connection !== "connected"
                      ? "Connect the local backend"
                        : !activeAsset
                          ? "Choose or import a recording"
                        : previewMode === "reel"
                          ? "Render a preview to watch the generated reel"
                          : playback?.status === "queued" ||
                              playback?.status === "running"
                            ? "Preparing browser preview"
                            : playback?.status === "failed"
                              ? "Browser preview needs a retry"
                              : "Source preview unavailable"}
                  </strong>
                  <small>
                    {previewMode !== "reel" && activeAsset
                      ? playbackStatusCopy(playback)
                      : "The console never substitutes demo footage for your real project."}
                  </small>
                  {previewMode !== "reel" &&
                    activeAsset &&
                    playback?.status === "failed" && (
                      <button
                        className="secondary-button"
                        onClick={() => void preparePlayback(projectId, assetId)}
                      >
                        Retry browser preview
                      </button>
                    )}
                </div>
              ) : (
                <div className="demo-frame">
                  <div className="game-hud">
                    <span>18 / 12 / 27</span>
                    <span>24:36</span>
                    <span>9 / 14 / 31</span>
                  </div>
                  <div className="demo-action" aria-hidden="true">
                    <span className="map-river" />
                    <span className="champion ally one">A</span>
                    <span className="champion ally two">A</span>
                    <span className="champion enemy three">E</span>
                    <span className="champion enemy four">E</span>
                    <span className="objective">BARON</span>
                  </div>
                  <div className="viewer-caption">
                    <span>
                      {previewMode === "source"
                        ? "Source frame"
                        : previewMode === "cut"
                          ? "Current cut"
                          : "Generated reel"}
                    </span>
                    <strong>{selected?.title ?? "Select a highlight"}</strong>
                  </div>
                </div>
              )}
            </div>
            {previewMode !== "reel" && (
              <div className="source-scrubber">
                <div className="source-scrubber-heading">
                  <span>
                    <strong>Whole source</strong>
                    <small>Drag to inspect any moment in the recording</small>
                  </span>
                  <output aria-live="polite">
                    {formatTimestamp(playheadSeconds)} /{" "}
                    {formatTimestamp(sourceDuration)}
                  </output>
                </div>
                <div className="source-scrub-track">
                  <div className="source-detections" aria-hidden="true">
                    {candidates.map((candidate) => (
                      <span
                        key={candidate.id}
                        className={`${candidate.decision} ${
                          candidate.id === selected?.id ? "selected" : ""
                        }`}
                        style={{
                          left: `${(candidate.start / sourceDuration) * 100}%`,
                          width: `${Math.max(
                            0.5,
                            ((candidate.end - candidate.start) /
                              sourceDuration) *
                              100,
                          )}%`,
                        }}
                      />
                    ))}
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={sourceDuration}
                    step="0.1"
                    value={Math.min(playheadSeconds, sourceDuration)}
                    onChange={(event) => {
                      setPreviewMode("source");
                      seekPreview(Number(event.target.value));
                    }}
                    aria-label="Whole source playhead"
                  />
                </div>
              </div>
            )}
          </section>

          <section className="timeline" aria-labelledby="timeline-title">
            <div className="timeline-header">
              <div>
                <span className="eyebrow">Review detected clips</span>
                <h2 id="timeline-title">
                  {candidates.length} proposed clips · {formatTime(reelDuration)}
                </h2>
                <small className={hasUnsavedTrims ? "unsaved-copy" : ""}>
                  {hasUnsavedTrims
                    ? "Unsaved trim changes"
                    : planRevisionNeeded
                      ? "Decisions changed · save a new plan version"
                      : "Source placement and exact cut are shown separately"}
                </small>
              </div>
              <div className="timeline-actions">
                <button
                  className="secondary-button"
                  onClick={startReviewer}
                  disabled={
                    !reviewerAvailable || (mode === "live" && !previewJobId)
                  }
                  title={
                    aiAssistance === "off"
                      ? "Turn AI assistance to Review to enable Agent 2"
                      : reviewerAvailable
                      ? "Run an optional independent review"
                      : "Configure a reviewer model to enable Agent 2"
                  }
                >
                  Optional Agent 2
                </button>
                <button
                  className="secondary-button"
                  onClick={savePlanRevision}
                  disabled={
                    !planRevisionNeeded ||
                    hasUnsavedTrims ||
                    pendingCount > 0 ||
                    Boolean(busy)
                  }
                >
                  Save plan revision
                </button>
              </div>
            </div>
            <div className="timeline-views">
              <section className="source-overview" aria-labelledby="source-overview-title">
                <div className="timeline-view-heading">
                  <div>
                    <span className="timeline-kicker">Whole recording</span>
                    <h3 id="source-overview-title">Source overview</h3>
                  </div>
                  <small>
                    Selected region:{" "}
                    {selected
                      ? `${formatTimestamp(selected.start)}–${formatTimestamp(selected.end)}`
                      : "None"}
                  </small>
                </div>
                <div className="timeline-ruler" aria-hidden="true">
                  {[0, 0.25, 0.5, 0.75, 1].map((position) => (
                    <span key={position}>{formatTime(sourceDuration * position)}</span>
                  ))}
                </div>
                <div className="clip-track" aria-label="Full source overview">
                  {candidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      style={{
                        left: `${Math.min(96, (candidate.start / sourceDuration) * 100)}%`,
                        width: `${Math.max(
                          1.5,
                          ((candidate.end - candidate.start) / sourceDuration) * 100,
                        )}%`,
                      }}
                      className={`${candidate.decision} ${
                        selected?.id === candidate.id ? "selected" : ""
                      }`}
                      onClick={() => {
                        selectCandidate(candidate);
                        selectPreviewMode("source");
                      }}
                      aria-label={`${candidate.title}, ${formatTime(candidate.start)} to ${formatTime(candidate.end)}, ${candidate.decision}`}
                    >
                      {candidate.index + 1}
                    </button>
                  ))}
                </div>
                <p className="timeline-help">
                  Each block is a detected highlight. Choose one to inspect its
                  exact cut below.
                </p>
              </section>

              {selected && (
                <section className="cut-view" aria-labelledby="cut-view-title">
                  <div className="timeline-view-heading">
                    <div>
                      <span className="timeline-kicker">Zoomed selection</span>
                      <h3 id="cut-view-title">Current cut</h3>
                    </div>
                    <strong>{formatTimestamp(selected.end - selected.start)}</strong>
                  </div>
                  <div className="trim-history-actions">
                    <button
                      className="text-button"
                      onClick={() =>
                        restoreTrimHistory(
                          (activeTrimHistory?.cursor ?? 0) - 1,
                        )
                      }
                      disabled={!canUndoTrim}
                    >
                      Undo
                    </button>
                    <button
                      className="text-button"
                      onClick={() =>
                        restoreTrimHistory(
                          (activeTrimHistory?.cursor ?? 0) + 1,
                        )
                      }
                      disabled={!canRedoTrim}
                    >
                      Redo
                    </button>
                    <button className="text-button" onClick={resetTrim}>
                      Reset trim
                    </button>
                    {trimDrafts[selected.id] && (
                      <span className="unsaved-badge">Unsaved</span>
                    )}
                  </div>
                  <div className="boundary-summary">
                    <strong>In {formatTimestamp(selected.start)}</strong>
                    <span>Highlighted area will be generated</span>
                    <strong>Out {formatTimestamp(selected.end)}</strong>
                  </div>
                  <div className="boundary-slider">
                    <div className="boundary-track" aria-hidden="true">
                      <span
                        style={{
                          left: `${startPercent}%`,
                          right: `${100 - endPercent}%`,
                        }}
                      />
                    </div>
                    <input
                      className="boundary-range boundary-range-start"
                      type="range"
                      min={boundaryWindowStart}
                      max={boundaryWindowEnd}
                      step="0.1"
                      value={selected.start}
                      onChange={(event) =>
                        changeBoundary("start", Number(event.target.value))
                      }
                      onPointerUp={() => recordTrimHistory()}
                      onKeyUp={() => recordTrimHistory()}
                      aria-label="Drag clip start"
                    />
                    <input
                      className="boundary-range boundary-range-end"
                      type="range"
                      min={boundaryWindowStart}
                      max={boundaryWindowEnd}
                      step="0.1"
                      value={selected.end}
                      onChange={(event) =>
                        changeBoundary("end", Number(event.target.value))
                      }
                      onPointerUp={() => recordTrimHistory()}
                      onKeyUp={() => recordTrimHistory()}
                      aria-label="Drag clip end"
                    />
                  </div>
                  <div className="boundary-context" aria-hidden="true">
                    <span>{formatTimestamp(boundaryWindowStart)}</span>
                    <span>Nearby source context</span>
                    <span>{formatTimestamp(boundaryWindowEnd)}</span>
                  </div>
                  <div className="cut-controls">
                    <div className="boundary-fields">
                      <label>
                        In <small>MM:SS.s</small>
                        <input
                          type="text"
                          inputMode="decimal"
                          value={boundaryDraft.start}
                          onChange={(event) =>
                            setBoundaryDraft((current) => ({
                              ...current,
                              start: event.target.value,
                            }))
                          }
                          onBlur={() => commitBoundary("start")}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") event.currentTarget.blur();
                          }}
                          aria-label="Clip start time in minutes and seconds"
                        />
                      </label>
                      <label>
                        Out <small>MM:SS.s</small>
                        <input
                          type="text"
                          inputMode="decimal"
                          value={boundaryDraft.end}
                          onChange={(event) =>
                            setBoundaryDraft((current) => ({
                              ...current,
                              end: event.target.value,
                            }))
                          }
                          onBlur={() => commitBoundary("end")}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") event.currentTarget.blur();
                          }}
                          aria-label="Clip end time in minutes and seconds"
                        />
                      </label>
                    </div>
                    <div className="boundary-actions">
                      <button
                        className="secondary-button"
                        onClick={() => previewBoundary("start")}
                      >
                        Check In
                      </button>
                      <button
                        className="secondary-button"
                        onClick={playSelectedCut}
                      >
                        Play cut
                      </button>
                      <button
                        className="secondary-button"
                        onClick={() => previewBoundary("end")}
                      >
                        Check Out
                      </button>
                    </div>
                  </div>
                  {boundaryError && (
                    <p className="boundary-error" role="alert">
                      {boundaryError}
                    </p>
                  )}
                  <button
                    className="secondary-button save-cut"
                    onClick={() => decide(selected, "adjust")}
                    disabled={!trimDrafts[selected.id] || Boolean(busy)}
                  >
                    Save cut decision
                  </button>
                </section>
              )}
            </div>
            <div className="clip-list">
              {candidates.map((candidate) => (
                <button
                  key={candidate.id}
                  className={`clip-row ${selected?.id === candidate.id ? "selected" : ""}`}
                  onClick={() => selectCandidate(candidate)}
                >
                  <span className={`decision-mark ${candidate.decision}`}>
                    {candidate.decision === "accept"
                      ? "✓"
                      : candidate.decision === "reject"
                        ? "×"
                        : candidate.decision === "adjust"
                          ? "↔"
                          : candidate.index + 1}
                  </span>
                  <span className="clip-copy">
                    <strong>{candidate.title}</strong>
                    <small>
                      {formatTimestamp(candidate.start)}–
                      {formatTimestamp(candidate.end)} ·{" "}
                      {formatTimestamp(candidate.end - candidate.start)}
                    </small>
                  </span>
                  <span className="clip-labels">
                    {candidate.labels.slice(0, 2).map((label) => (
                      <small key={label}>{label}</small>
                    ))}
                  </span>
                  <span className="score">{Math.round(candidate.score * 100)}</span>
                </button>
              ))}
            </div>
          </section>
        </section>

        <aside className={`inspector ${rightOpen ? "open" : ""}`}>
          <div className="inspector-header">
            <div>
              <span className="eyebrow">Evidence inspector</span>
              <h2>{selected?.title ?? "No clip selected"}</h2>
            </div>
            <button
              className="close-inspector"
              onClick={() => setRightOpen(false)}
              aria-label="Close evidence inspector"
            >
              Close
            </button>
          </div>

          {selected && (
            <>
              <section className="inspector-section">
                <div className="score-block">
                  <div>
                    <span>Candidate score</span>
                    <strong>{Math.round(selected.score * 100)}</strong>
                  </div>
                  <div className="score-bar" aria-hidden="true">
                    <span style={{ width: `${selected.score * 100}%` }} />
                  </div>
                </div>
                <dl className="evidence-grid">
                  <div>
                    <dt>Source in</dt>
                    <dd>{formatTimestamp(selected.start)}</dd>
                  </div>
                  <div>
                    <dt>Source out</dt>
                    <dd>{formatTimestamp(selected.end)}</dd>
                  </div>
                  <div>
                    <dt>Duration</dt>
                    <dd>{formatTimestamp(selected.end - selected.start)}</dd>
                  </div>
                  <div>
                    <dt>Signals</dt>
                    <dd>{selected.signalIds.length}</dd>
                  </div>
                </dl>
              </section>

              <section className="inspector-section">
                <h3>Why it was selected</h3>
                <ul className="signal-list">
                  {selected.signalIds.map((signalId, index) => {
                    const signal = review?.session.signals.find(
                      (item) => item.id === signalId,
                    );
                    return (
                      <li key={signalId}>
                        <button
                          className="signal-row"
                          onClick={() => {
                            setPreviewMode("source");
                            window.requestAnimationFrame(() =>
                              seekPreview(
                                signal?.timestamp_seconds ?? selected.start,
                              ),
                            );
                          }}
                          title="Seek source preview to this evidence"
                        >
                          <span className="signal-icon" aria-hidden="true">
                            {index + 1}
                          </span>
                          <span>
                            <strong>
                              {signal?.event_name
                                ? statusLabel(signal.event_name)
                                : index
                                  ? "Audio reaction peak"
                                  : "HUD event"}
                            </strong>
                            <small>
                              {formatTimestamp(
                                signal?.timestamp_seconds ?? selected.start,
                              )}{" "}
                              · {signal?.source ?? signalId} ·{" "}
                              {Math.round(
                                (signal?.confidence ?? selected.score) * 100,
                              )}
                              % confidence
                            </small>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>

              <section className="inspector-section">
                <h3>Human decision</h3>
                <div className="decision-buttons">
                  <button
                    className={selected.decision === "accept" ? "active accept" : ""}
                    disabled={selectedIsManual}
                    onClick={() => decide(selected, "accept")}
                  >
                    Accept
                    <kbd>A</kbd>
                  </button>
                  <button
                    className={selected.decision === "reject" ? "active reject" : ""}
                    disabled={selectedIsManual}
                    onClick={() => decide(selected, "reject")}
                  >
                    Reject
                    <kbd>R</kbd>
                  </button>
                </div>
                {selectedIsManual ? (
                  <p className="helper-copy">
                    This manually added clip is included. Adjust its In/Out
                    points, then save a new plan version.
                  </p>
                ) : null}
              </section>

              <section className="inspector-section">
                <div className="section-heading missed-heading">
                  <h3>Add a missed highlight</h3>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() =>
                      setMissedDraft((current) => ({
                        ...current,
                        start: formatTimestamp(
                          Math.max(0, playheadSeconds - 5),
                        ),
                        end: formatTimestamp(
                          Math.min(sourceDuration, playheadSeconds + 10),
                        ),
                      }))
                    }
                  >
                    Use playhead
                  </button>
                </div>
                <form className="missed-form" onSubmit={addMissedHighlight}>
                  <label>
                    Event
                    <input
                      value={missedDraft.event}
                      onChange={(event) =>
                        setMissedDraft((current) => ({
                          ...current,
                          event: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <div className="missed-range">
                    <label>
                      In <small>MM:SS.s</small>
                      <input
                        value={missedDraft.start}
                        placeholder="12:34.5"
                        onChange={(event) =>
                          setMissedDraft((current) => ({
                            ...current,
                            start: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label>
                      Out <small>MM:SS.s</small>
                      <input
                        value={missedDraft.end}
                        placeholder="12:49.5"
                        onChange={(event) =>
                          setMissedDraft((current) => ({
                            ...current,
                            end: event.target.value,
                          }))
                        }
                      />
                    </label>
                  </div>
                  <button
                    className="secondary-button full"
                    disabled={
                      !missedDraft.start ||
                      !missedDraft.end ||
                      Boolean(busy)
                    }
                  >
                    Add to review
                  </button>
                </form>
              </section>
            </>
          )}

          <section className="inspector-section reviewer-panel">
            <div className="section-heading">
              <h3>Agent 2 verdict</h3>
              {workflow && (
                <span className={`workflow-state ${workflow.state}`}>
                  {statusLabel(workflow.state)}
                </span>
              )}
            </div>
            {workflow?.rounds.at(-1)?.verdict ? (
              <>
                <p>{workflow.rounds.at(-1)?.verdict?.summary}</p>
                <small>
                  {Math.round(
                    (workflow.rounds.at(-1)?.verdict?.confidence ?? 0) * 100,
                  )}
                  % confidence · Round {workflow.rounds.at(-1)?.round_number}
                </small>
              </>
            ) : (
              <p>
                {aiAssistance === "off"
                  ? "AI assistance is off. Manual review and exact-version human approval remain fully available."
                  : reviewerAvailable
                  ? "Optional: run the independent reviewer after the preview is ready. Agent approval never replaces your approval."
                  : "Review mode is selected, but no local reviewer model is configured. Manual review remains fully available."}
              </p>
            )}
            {workflow?.error && <p className="inline-error">{workflow.error}</p>}
          </section>
        </aside>
      </div>

      <footer className={`approval-bar ${setupNeeded ? "setup-hidden" : ""}`}>
        <div className="approval-summary" role="status" aria-live="polite">
          <span className={error ? "error-indicator" : "status-indicator"} />
          <div>
            <strong>{error || busy || notice}</strong>
            <small>
              {acceptedCount} kept · {rejectedCount} rejected · {pendingCount} pending
              {finalJob ? ` · Final ${statusLabel(finalJob.status)}` : ""}
            </small>
          </div>
          {error && (
            <button className="text-button" onClick={() => setError("")}>
              Dismiss
            </button>
          )}
        </div>
        <div className="approval-actions">
          {finalJob?.status === "succeeded" && (
            <div className="output-actions" aria-label="Final output actions">
              <button
                className="secondary-button"
                onClick={() => setPreviewMode("reel")}
              >
                Watch final
              </button>
              {finalDownloadUrl ? (
                <a
                  className="secondary-button"
                  href={finalDownloadUrl}
                >
                  Download
                </a>
              ) : (
                <button className="secondary-button" disabled>
                  Download
                </button>
              )}
              <button
                className="secondary-button"
                disabled={!desktopMode || !finalJob.output_path}
                onClick={() => void revealFinalOutput()}
                title={
                  desktopMode
                    ? "Show the final render in Finder"
                    : "Available in the Cutroom desktop app"
                }
              >
                Open folder
              </button>
            </div>
          )}
          <button
            className="secondary-button"
            onClick={() => void renderPreview(plan?.id ?? "demo-plan")}
            disabled={mode === "live" && !plan}
          >
            Refresh preview
          </button>
          <button
            className="approval-button"
            onClick={approvePlan}
            disabled={
              pendingCount > 0 ||
              hasUnsavedTrims ||
              planRevisionNeeded ||
              Boolean(busy)
            }
          >
            {isApproved && !planRevisionNeeded && !hasUnsavedTrims
              ? "Current plan approved"
              : "Approve current plan"}
          </button>
          <button
            className="primary-button"
            onClick={renderFinal}
            disabled={
              !isApproved ||
              hasUnsavedTrims ||
              planRevisionNeeded ||
              Boolean(busy)
            }
          >
            Render final
          </button>
        </div>
      </footer>
    </main>
  );
}
