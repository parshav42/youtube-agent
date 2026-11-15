export type JobStatus =
  "queued" | "running" | "cancelling" | "cancelled" | "complete" | "failed";

export type JobStage =
  | "dialogue"
  | "images"
  | "speech"
  | "video"
  | "cancelling"
  | "cancelled"
  | "complete"
  | "failed";

export interface VideoResult {
  videoUrl: string;
  dialogueUrl: string;
  audioUrl: string;
  imageUrls: string[];
  outputFolder: string;
}

export interface VideoJob {
  jobId: string;
  idea: string;
  status: JobStatus;
  stage: JobStage;
  message: string;
  error: string | null;
  result: VideoResult | null;
}

interface ApiError {
  error?: string;
}

const API_BASE_URL = (import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "");

function apiUrl(path: string) {
  return `${API_BASE_URL}${path}`;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as ApiError;
      if (body.error) message = body.error;
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function withAssetBase(job: VideoJob): VideoJob {
  if (!job.result || !API_BASE_URL) return job;
  const assetUrl = (url: string) =>
    url.startsWith("/") ? `${API_BASE_URL}${url}` : url;
  return {
    ...job,
    result: {
      ...job.result,
      videoUrl: assetUrl(job.result.videoUrl),
      dialogueUrl: assetUrl(job.result.dialogueUrl),
      audioUrl: assetUrl(job.result.audioUrl),
      imageUrls: job.result.imageUrls.map(assetUrl),
    },
  };
}

export const api = {
  async createVideo(idea: string): Promise<VideoJob> {
    const job = await request<VideoJob>(apiUrl("/api/videos"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idea }),
    });
    return withAssetBase(job);
  },

  async getJob(jobId: string, signal?: AbortSignal): Promise<VideoJob> {
    const job = await request<VideoJob>(
      apiUrl(`/api/jobs/${encodeURIComponent(jobId)}`),
      {
        signal,
      },
    );
    return withAssetBase(job);
  },

  cancelJob(jobId: string): Promise<VideoJob> {
    return request<VideoJob>(
      apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/cancel`),
      {
        method: "POST",
      },
    );
  },
};

export function isJobActive(status: JobStatus): boolean {
  return status === "queued" || status === "running" || status === "cancelling";
}
