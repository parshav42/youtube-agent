import { useState, type ReactNode } from "react";
import { mediaContent } from "../content/content";
import { media, type MediaSlotId } from "../content/media";

const particles = Array.from({ length: 10 }, (_, index) => index);

export function MediaSlot({
  id,
  className = "",
  children,
}: {
  id: MediaSlotId;
  className?: string;
  children?: ReactNode;
}) {
  const config = media[id];
  const [failed, setFailed] = useState(false);
  const hasSource =
    config.src !== null && Boolean(config.src.webm || config.src.mp4);

  return (
    <div
      className={`media-slot media-variant-${config.variant} ${className}`.trim()}
      data-media-id={id}
    >
      {hasSource && !failed ? (
        <video
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          poster={config.poster ?? undefined}
          onError={() => setFailed(true)}
          aria-hidden="true"
        >
          {config.src?.webm && (
            <source src={config.src.webm} type="video/webm" />
          )}
          {config.src?.mp4 && <source src={config.src.mp4} type="video/mp4" />}
        </video>
      ) : (
        <div className="media-placeholder" aria-hidden="true">
          <div className="media-mesh" />
          <div className="media-orb media-orb-one" />
          <div className="media-orb media-orb-two" />
          <div className="media-orb media-orb-three" />
          <div className="media-filmstrip">
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
          <div className="media-particles">
            {particles.map((particle) => (
              <i key={particle} />
            ))}
          </div>
        </div>
      )}
      {import.meta.env.DEV && !hasSource && (
        <span className="media-coming-soon">{mediaContent.comingSoon}</span>
      )}
      {children}
    </div>
  );
}
