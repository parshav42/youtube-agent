import { memo } from "react";
import "./UtubeAgentLoader.css";

export type UtubeAgentLoaderSize = "small" | "medium" | "large";
export type UtubeAgentLoaderBackground = "transparent" | "white";

export interface UtubeAgentLoaderProps {
  size?: UtubeAgentLoaderSize;
  background?: UtubeAgentLoaderBackground;
  text?: string | null;
  progress?: number;
  fullScreen?: boolean;
  className?: string;
}

function UtubeAgentLoaderComponent({
  size = "medium",
  background = "transparent",
  text = "Initializing AI Agents...",
  progress,
  fullScreen = false,
  className = "",
}: UtubeAgentLoaderProps) {
  const progressValue =
    progress === undefined || !Number.isFinite(progress)
      ? undefined
      : Math.round(Math.max(0, Math.min(100, progress)));
  const classes = [
    "utube-agent-loader",
    `utube-agent-loader--${size}`,
    `utube-agent-loader--${background}`,
    fullScreen ? "utube-agent-loader--fullscreen" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={text ?? "Loading UTubeAgent"}
    >
      <svg
        className="utube-agent-loader__mark"
        viewBox="0 0 120 120"
        aria-hidden="true"
        focusable="false"
      >
        <path
          className="utube-agent-loader__u-track"
          d="M25 14v44c0 25 14 38 35 38s35-13 35-38V14"
        />
        <path
          className="utube-agent-loader__u-draw"
          d="M25 14v44c0 25 14 38 35 38s35-13 35-38V14"
        />
        <ellipse
          className="utube-agent-loader__orbit"
          cx="60"
          cy="60"
          rx="49"
          ry="31"
          transform="rotate(-32 60 60)"
        />
        <g className="utube-agent-loader__particle-rotator">
          <circle
            className="utube-agent-loader__particle"
            cx="60"
            cy="29"
            r="3.4"
          />
        </g>
        <path
          className="utube-agent-loader__blue-pulse"
          d="M36 29v29c0 17 9 26 24 26s24-9 24-26V29"
        />
      </svg>

      {text && <span className="utube-agent-loader__text">{text}</span>}

      {progressValue !== undefined && (
        <div
          className="utube-agent-loader__progress"
          role="progressbar"
          aria-label="Loading progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressValue}
        >
          <span style={{ width: `${progressValue}%` }} />
        </div>
      )}
    </div>
  );
}

export const UtubeAgentLoader = memo(UtubeAgentLoaderComponent);
