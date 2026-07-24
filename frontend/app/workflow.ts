export type WorkflowStep = {
  label: "Source" | "Brief" | "Analyse" | "Review" | "Export";
  complete: boolean;
  current: boolean;
};

export type WorkflowFacts = {
  sourceReady: boolean;
  briefReady: boolean;
  analysisReady: boolean;
  decisionsComplete: boolean;
  hasUnsavedChanges: boolean;
  planRevisionNeeded: boolean;
  humanApproved: boolean;
  finalRenderSucceeded: boolean;
};

export function deriveWorkflowSteps(facts: WorkflowFacts): WorkflowStep[] {
  const reviewReady =
    facts.analysisReady &&
    facts.decisionsComplete &&
    !facts.hasUnsavedChanges &&
    !facts.planRevisionNeeded &&
    facts.humanApproved;

  return [
    {
      label: "Source",
      complete: facts.sourceReady,
      current: !facts.sourceReady,
    },
    {
      label: "Brief",
      complete: facts.sourceReady && facts.briefReady,
      current: facts.sourceReady && !facts.briefReady,
    },
    {
      label: "Analyse",
      complete: facts.analysisReady,
      current:
        facts.sourceReady && facts.briefReady && !facts.analysisReady,
    },
    {
      label: "Review",
      complete: reviewReady,
      current: facts.analysisReady && !reviewReady,
    },
    {
      label: "Export",
      complete: facts.finalRenderSucceeded,
      current: reviewReady && !facts.finalRenderSucceeded,
    },
  ];
}

