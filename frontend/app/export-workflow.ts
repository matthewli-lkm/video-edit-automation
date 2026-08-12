type JobLike = { status: string };

export type Delay = (milliseconds: number) => Promise<void>;

const defaultDelay: Delay = (milliseconds) =>
  new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));

export async function waitForTerminalJob<T extends JobLike>(
  loadJob: () => Promise<T>,
  onUpdate: (job: T) => void = () => undefined,
  delay: Delay = defaultDelay,
  pollIntervalMilliseconds = 750,
): Promise<T> {
  while (true) {
    const job = await loadJob();
    onUpdate(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await delay(pollIntervalMilliseconds);
  }
}

export async function runSequentialTasks<T, Result>(
  items: readonly T[],
  run: (item: T, index: number) => Promise<Result>,
): Promise<Result[]> {
  const results: Result[] = [];
  for (const [index, item] of items.entries()) {
    results.push(await run(item, index));
  }
  return results;
}
