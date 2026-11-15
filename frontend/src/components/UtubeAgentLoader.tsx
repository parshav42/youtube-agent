import { memo, useId } from "react";
import "./UtubeAgentLoader.css";

export type UtubeAgentLoaderSize = "small" | "medium" | "large";
export type UtubeAgentLoaderBackground = "transparent" | "white";

export interface UtubeAgentLoaderProps {
  size?: UtubeAgentLoaderSize;
  background?: UtubeAgentLoaderBackground;
  showText?: boolean;
  text?: string;
  progress?: number;
  fullScreen?: boolean;
  className?: string;
}

function UtubeAgentLoaderComponent({
  size = "medium",
  background = "transparent",
  showText = true,
  text = "Initializing AI Agents...",
  progress,
  fullScreen = false,
  className = "",
}: UtubeAgentLoaderProps) {
  const filterId = `utube-agent-glow-${useId().replace(/:/g, "")}`;
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
      aria-busy={progressValue !== 100}
      aria-label={showText && text ? text : "Loading UtubeAgent"}
    >
      <svg
        className="utube-agent-loader__mark"
        viewBox="0 0 120 120"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <filter
            id={filterId}
            x="-60%"
            y="-40%"
            width="220%"
            height="180%"
            colorInterpolationFilters="sRGB"
          >
            <feGaussianBlur stdDeviation="2.2" result="glow" />
            <feMerge>
              <feMergeNode in="glow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <path
          className="utube-agent-loader__u-track"
          d="M27 15v44c0 24 13 36 33 36s33-12 33-36V15"
        />
        <path
          className="utube-agent-loader__u-draw"
          d="M27 15v44c0 24 13 36 33 36s33-12 33-36V15"
          filter={`url(#${filterId})`}
        />
        <path
          className="utube-agent-loader__orbit"
          d="M12 71c14-25 62-30 96-45-12 25-47 46-88 61-8 3-13-8-8-16Z"
        />
        <circle className="utube-agent-loader__particle" cx="0" cy="0" r="3.2">
          <animateMotion
            dur="5s"
            repeatCount="indefinite"
            calcMode="linear"
            keyTimes="0;0.23;0.25;0.5;0.52;1"
            keyPoints="0;0;0;1;1;0"
            path="M12 71c14-25 62-30 96-45-12 25-47 46-88 61-8 3-13-8-8-16Z"
          />
        </circle>
        <path
          className="utube-agent-loader__blue-pulse"
          d="M37 30v28c0 16 8 24 23 24s23-8 23-24V30"
          filter={`url(#${filterId})`}
        />
        <circle
          className="utube-agent-loader__blue-particle"
          cx="0"
          cy="0"
          r="2.8"
        >
          <animateMotion
            dur="5s"
            repeatCount="indefinite"
            calcMode="linear"
            keyTimes="0;0.5;0.52;0.76;0.78;1"
            keyPoints="0;0;0;1;1;0"
            path="M37 30v28c0 16 8 24 23 24s23-8 23-24V30"
          />
        </circle>
      </svg>

      {showText && text && (
        <span className="utube-agent-loader__text">{text}</span>
      )}

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
