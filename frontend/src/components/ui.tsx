import {
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { Check, Maximize, Pause, Play, X } from "lucide-react";
import { mediaContent } from "../content/content";
import { media } from "../content/media";
import { MediaSlot } from "./MediaSlot";
import { steps } from "../content/content";
import type { JobStatus, JobStage } from "../lib/api";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  to?: string;
  href?: string;
  variant?: "primary" | "secondary" | "quiet";
  children: ReactNode;
};

export function Button({
  to,
  href,
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonProps) {
  const classes = `button button-${variant} ${className}`.trim();
  if (to)
    return (
      <Link className={classes} to={to}>
        {children}
      </Link>
    );
  if (href)
    return (
      <a className={classes} href={href}>
        {children}
      </a>
    );
  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}

export function Section({
  id,
  eyebrow,
  title,
  description,
  children,
  className = "",
}: {
  id?: string;
  eyebrow?: string;
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`section ${className}`.trim()}>
      {(eyebrow || title || description) && (
        <div className="section-heading">
          {eyebrow && <p className="eyebrow">{eyebrow}</p>}
          {title && <h2>{title}</h2>}
          {description && <p className="section-description">{description}</p>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <article className={`glass-card ${className}`.trim()}>{children}</article>
  );
}

export function Marquee({ items }: { items: string[] }) {
  return (
    <div
      className="marquee"
      role="list"
      aria-label={`Built with ${items.join(", ")}`}
    >
      <div className="marquee-track" aria-hidden="true">
        {[...items, ...items].map((item, index) => (
          <span className="marquee-item" key={`${item}-${index}`}>
            <i aria-hidden="true" />
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

export function Accordion({
  items,
}: {
  items: { question: string; answer: string }[];
}) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="accordion">
      {items.map((item, index) => (
        <div
          className={`accordion-item ${open === index ? "is-open" : ""}`}
          key={item.question}
        >
          <h3>
            <button
              type="button"
              aria-expanded={open === index}
              aria-controls={`faq-answer-${index}`}
              onClick={() => setOpen(open === index ? null : index)}
            >
              <span>{item.question}</span>
              <span className="accordion-plus" aria-hidden="true" />
            </button>
          </h3>
          <div
            className="accordion-answer"
            id={`faq-answer-${index}`}
            hidden={open !== index}
          >
            <p>{item.answer}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

export function VideoPlayer({
  className = "",
  label = mediaContent.playPreview,
}: {
  className?: string;
  label?: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [fullscreenError, setFullscreenError] = useState(false);
  const duration = 48;
  const hasSource =
    media.demo.src !== null &&
    Boolean(media.demo.src.webm || media.demo.src.mp4);

  useEffect(() => {
    if (!playing || hasSource) return;
    const timer = window.setInterval(() => {
      setElapsed((current) => (current >= duration - 1 ? 0 : current + 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [hasSource, playing]);

  const togglePlayback = async () => {
    if (!hasSource || failed) {
      setElapsed((current) => {
        if (current >= duration) return 0;
        return current;
      });
      setPlaying((current) => !current);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      try {
        await video.play();
      } catch {
        setFailed(true);
      }
    } else {
      video.pause();
    }
  };

  const toggleFullscreen = async () => {
    setFullscreenError(false);
    try {
      if (!document.fullscreenElement)
        await frameRef.current?.requestFullscreen();
      else await document.exitFullscreen();
    } catch {
      setFullscreenError(true);
    }
  };

  const seek = (value: number) => {
    setElapsed(value);
    if (videoRef.current && hasSource) videoRef.current.currentTime = value;
  };

  const formattedTime = (time: number) =>
    `${String(Math.floor(time / 60)).padStart(2, "0")}:${String(time % 60).padStart(2, "0")}`;

  return (
    <div className={`video-frame ${className}`.trim()} ref={frameRef}>
      {hasSource && !failed ? (
        <video
          ref={videoRef}
          poster={media.demo.poster ?? undefined}
          preload="metadata"
          playsInline
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={() =>
            setElapsed(Math.floor(videoRef.current?.currentTime ?? 0))
          }
          onError={() => setFailed(true)}
        >
          {media.demo.src?.webm && (
            <source src={media.demo.src.webm} type="video/webm" />
          )}
          {media.demo.src?.mp4 && (
            <source src={media.demo.src.mp4} type="video/mp4" />
          )}
          Your browser does not support embedded video.
        </video>
      ) : (
        <>
          <MediaSlot id="demo" className="demo-media-slot" />
          <div className="demo-preview-copy">
            <span>{mediaContent.demoPreview}</span>
            <strong>{mediaContent.demoTitle}</strong>
            <small>{mediaContent.demoDescription}</small>
            <div
              className={`demo-timeline ${playing ? "is-playing" : ""}`}
              aria-hidden="true"
            >
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
            </div>
          </div>
        </>
      )}
      <div className="video-vignette" aria-hidden="true" />
      <button
        type="button"
        className={`video-play-overlay ${playing ? "is-playing" : ""}`}
        onClick={() => void togglePlayback()}
        aria-label={playing ? mediaContent.pausePreview : label}
      >
        {playing ? (
          <Pause size={18} fill="currentColor" />
        ) : (
          <Play size={18} fill="currentColor" />
        )}
      </button>
      <div className="video-controls">
        <button
          type="button"
          className="video-control-play"
          onClick={() => void togglePlayback()}
          aria-label={playing ? mediaContent.pausePreview : label}
        >
          {playing ? (
            <Pause size={15} fill="currentColor" />
          ) : (
            <Play size={15} fill="currentColor" />
          )}
        </button>
        <span className="video-time">{formattedTime(elapsed)}</span>
        <input
          aria-label="Seek demo preview"
          type="range"
          min={0}
          max={duration}
          value={elapsed}
          onChange={(event) => seek(Number(event.target.value))}
          style={
            {
              "--video-progress": `${(elapsed / duration) * 100}%`,
            } as CSSProperties
          }
        />
        <span className="video-time">{formattedTime(duration)}</span>
        <button
          type="button"
          className="video-control-fullscreen"
          onClick={() => void toggleFullscreen()}
          aria-label={mediaContent.fullscreen}
        >
          <Maximize size={15} />
        </button>
      </div>
      {fullscreenError && (
        <p className="fullscreen-error" role="status">
          Fullscreen is not available in this browser.
        </p>
      )}
    </div>
  );
}

export function Stepper({
  status,
  stage,
}: {
  status: JobStatus | null;
  stage: JobStage | null;
}) {
  const activeIndex = steps.findIndex((step) => step.id === stage);
  return (
    <ol className="generation-steps">
      {steps.map((step, index) => {
        const done =
          status === "complete" || (activeIndex >= 0 && index < activeIndex);
        const current =
          !["complete", "failed", "cancelled", "cancelling"].includes(
            status ?? "",
          ) && index === activeIndex;
        const failed = status === "failed" && index === activeIndex;
        return (
          <li
            className={`generation-step ${done ? "done" : ""} ${current ? "current" : ""} ${failed ? "failed" : ""}`}
            key={step.id}
          >
            <span className="step-marker" aria-hidden="true">
              {done ? <Check size={14} /> : step.number}
            </span>
            <span className="step-copy">
              <strong>{step.title}</strong>
              <small>{step.description}</small>
            </span>
            <span className="step-state">
              {done
                ? "DONE"
                : current
                  ? "ACTIVE"
                  : failed
                    ? "FAILED"
                    : "PENDING"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function Modal({
  open,
  onClose,
  children,
  label,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  label: string;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const focusable = document.querySelectorAll<HTMLElement>(
        ".modal-dialog button:not(:disabled), .modal-dialog a[href], .modal-dialog input:not(:disabled), .modal-dialog textarea:not(:disabled), .modal-dialog [tabindex]:not([tabindex='-1'])",
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        <button
          className="modal-close"
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label="Close"
        >
          <X size={18} />
        </button>
        {children}
      </section>
    </div>
  );
}
