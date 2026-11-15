import { useCallback, useEffect, useRef, useState } from "react";
import { api, isJobActive, type JobStage, type VideoJob } from "../lib/api";

const activeStages: JobStage[] = ["dialogue", "images", "speech", "video"];

export function useJob() {
  const [job, setJob] = useState<VideoJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastStage = useRef<JobStage>("dialogue");
  const active = job !== null && isJobActive(job.status);

  useEffect(() => {
    if (!job?.jobId || !active) return;

    let mounted = true;
    let timer: number | undefined;
    const controller = new AbortController();

    const poll = async () => {
      try {
        const latest = await api.getJob(job.jobId, controller.signal);
        if (!mounted) return;
        if (activeStages.includes(latest.stage))
          lastStage.current = latest.stage;
        const visibleJob =
          latest.status === "failed" && !activeStages.includes(latest.stage)
            ? { ...latest, stage: lastStage.current }
            : latest;
        setJob(visibleJob);
        if (isJobActive(latest.status)) {
          timer = window.setTimeout(poll, 900);
        }
      } catch (pollError) {
        if (!mounted || controller.signal.aborted) return;
        const message =
          pollError instanceof Error
            ? pollError.message
            : "Could not check video progress.";
        setError(message);
        setJob((current) =>
          current
            ? {
                ...current,
                status: "failed",
                stage: current.stage,
                message,
                error: message,
              }
            : current,
        );
      }
    };

    timer = window.setTimeout(poll, 900);
    return () => {
      mounted = false;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [active, job?.jobId]);

  const create = useCallback(async (idea: string) => {
    setSubmitting(true);
    setError(null);
    setJob(null);
    try {
      const created = await api.createVideo(idea);
      if (activeStages.includes(created.stage))
        lastStage.current = created.stage;
      setJob(created);
    } catch (createError) {
      setError(
        createError instanceof Error
          ? createError.message
          : "Could not start video generation.",
      );
    } finally {
      setSubmitting(false);
    }
  }, []);

  const stop = useCallback(async () => {
    if (
      !job ||
      !isJobActive(job.status) ||
      job.status === "cancelling" ||
      stopping
    )
      return;
    setStopping(true);
    setError(null);
    try {
      setJob(await api.cancelJob(job.jobId));
    } catch (stopError) {
      setError(
        stopError instanceof Error
          ? stopError.message
          : "Could not stop video generation.",
      );
    } finally {
      setStopping(false);
    }
  }, [job, stopping]);

  const clearError = useCallback(() => setError(null), []);

  return {
    job,
    setJob,
    active,
    submitting,
    stopping,
    error,
    create,
    stop,
    clearError,
  };
}
