export type MediaSlotId =
  "heroLoop" | "step1" | "step2" | "step3" | "step4" | "demo" | "ctaLoop";

export type MediaVariant =
  "hero" | "story" | "shots" | "voice" | "assemble" | "demo" | "cta";

export interface MediaSource {
  webm?: string;
  mp4?: string;
}

export interface MediaConfig {
  src: MediaSource | null;
  poster: string | null;
  variant: MediaVariant;
}

export const media: Record<MediaSlotId, MediaConfig> = {
  heroLoop: { src: null, poster: null, variant: "hero" },
  step1: { src: null, poster: null, variant: "story" },
  step2: { src: null, poster: null, variant: "shots" },
  step3: { src: null, poster: null, variant: "voice" },
  step4: { src: null, poster: null, variant: "assemble" },
  demo: { src: null, poster: null, variant: "demo" },
  ctaLoop: { src: null, poster: null, variant: "cta" },
};
