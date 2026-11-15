# Media slots

The site ships without promotional video files. Its hero, four story beats, demo,
and final call-to-action use animated CSS placeholders until media is supplied.
The slot definitions live in [`frontend/src/content/media.ts`](frontend/src/content/media.ts);
the reusable renderer is [`frontend/src/components/MediaSlot.tsx`](frontend/src/components/MediaSlot.tsx).

## Add a video

1. Put optimized files in `frontend/public/media/`:

   | Slot | Suggested files |
   | --- | --- |
   | `heroLoop` | `hero-loop.webm`, `hero-loop.mp4`, `hero-poster.webp` |
   | `step1` | `step-1.webm`, `step-1.mp4`, `step-1-poster.webp` |
   | `step2` | `step-2.webm`, `step-2.mp4`, `step-2-poster.webp` |
   | `step3` | `step-3.webm`, `step-3.mp4`, `step-3-poster.webp` |
   | `step4` | `step-4.webm`, `step-4.mp4`, `step-4-poster.webp` |
   | `demo` | `demo.webm`, `demo.mp4`, `demo-poster.webp` |
   | `ctaLoop` | `cta-loop.webm`, `cta-loop.mp4`, `cta-poster.webp` |

2. Set the slot's `src` and `poster` in `media.ts`, for example:

   ```ts
   heroLoop: {
     src: { webm: "/media/hero-loop.webm", mp4: "/media/hero-loop.mp4" },
     poster: "/media/hero-poster.webp",
     variant: "hero",
   },
   ```

3. Keep ambient loops short, silent, and seamless. Prefer 1920×1080 or smaller,
   24–30 fps, WebM plus MP4 fallback, and a poster under 300 KB. Aim for 2–5 MB
   per loop and under 15 MB for the demo. `preload="metadata"` avoids eagerly
   downloading the full file.

Example H.264 MP4 compression:

```bash
ffmpeg -i input.mp4 -vf "scale='min(1920,iw)':-2" -r 24 \
  -c:v libx264 -preset slow -crf 27 -an -movflags +faststart \
  frontend/public/media/hero-loop.mp4
```

Example WebM version:

```bash
ffmpeg -i input.mp4 -vf "scale='min(1920,iw)':-2" -r 24 \
  -c:v libvpx-vp9 -crf 34 -b:v 0 -an \
  frontend/public/media/hero-loop.webm
```

The `MediaSlot` keeps its variant-specific animated placeholder if a source is
unset or cannot load. No component or page changes are needed to swap a slot.
