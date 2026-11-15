import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import {
  AnimatePresence,
  motion,
  MotionConfig,
  useReducedMotion,
} from "framer-motion";
import {
  ArrowDown,
  ArrowDownRight,
  ArrowRight,
  AudioLines,
  Check,
  Download,
  Film,
  Image as ImageIcon,
  Menu,
  Pause,
  Play,
  ShieldCheck,
  Sparkles,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import {
  Accordion,
  Button,
  Card,
  Marquee,
  Modal,
  Section,
  Stepper,
  VideoPlayer,
} from "./components/ui";
import { MediaSlot } from "./components/MediaSlot";
import { UtubeAgentLoader } from "./components/UtubeAgentLoader";
import {
  brand,
  contactCopy,
  creatorNotes,
  faqs,
  features,
  footerGroups,
  homeContent,
  legalCopy,
  navigation,
  pageSeo,
  pricing,
  steps,
  technologies,
} from "./content/content";
import type { MediaSlotId } from "./content/media";
import { useJob } from "./hooks/useJob";
import type { JobStage, JobStatus } from "./lib/api";

gsap.registerPlugin(ScrollTrigger);

const iconMap = {
  sparkles: Sparkles,
  image: ImageIcon,
  audio: AudioLines,
  stop: Pause,
  download: Download,
  shield: ShieldCheck,
};

const stepMediaIds: MediaSlotId[] = ["step1", "step2", "step3", "step4"];

function usePageEffects() {
  const location = useLocation();

  useEffect(() => {
    const meta = pageSeo[location.pathname] ?? {
      title: "Page not found — UtubeAgent",
      description: brand.description,
    };
    document.title = meta.title;
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", meta.description);
    document
      .querySelector('meta[property="og:title"]')
      ?.setAttribute("content", meta.title);
    document
      .querySelector('meta[property="og:description"]')
      ?.setAttribute("content", meta.description);
  }, [location.pathname]);

  useEffect(() => {
    window.scrollTo(0, 0);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const lenis = new Lenis({ duration: 1.05, smoothWheel: true });
    const onScroll = () => ScrollTrigger.update();
    const tick = (time: number) => lenis.raf(time * 1000);
    lenis.on("scroll", onScroll);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);

    return () => {
      lenis.off("scroll", onScroll);
      gsap.ticker.remove(tick);
      lenis.destroy();
    };
  }, []);

  useEffect(() => {
    if (location.hash) {
      window.setTimeout(() => {
        document
          .querySelector(location.hash)
          ?.scrollIntoView({ behavior: "smooth" });
      }, 80);
    }
  }, [location]);
}

function BrandLogo({ onClick }: { onClick?: () => void }) {
  return (
    <Link
      className="brand"
      to="/"
      aria-label={`${brand.name} home`}
      onClick={onClick}
    >
      <span className="brand-wordmark">
        <span className="brand-name" aria-hidden="true">
          <span className="brand-u">U</span>
          <span className="brand-tube">tube</span>
          <span className="brand-agent">Agent</span>
        </span>
        <span className="brand-tagline">Create · Automate · Grow</span>
      </span>
    </Link>
  );
}

function Header() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 24);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  useEffect(() => {
    document.body.classList.toggle("menu-open", menuOpen);
    return () => document.body.classList.remove("menu-open");
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  const closeMenu = () => setMenuOpen(false);

  return (
    <header
      className={`site-header ${scrolled ? "is-scrolled" : ""} ${menuOpen ? "menu-visible" : ""}`}
    >
      <div className="nav-wrap">
        <BrandLogo onClick={closeMenu} />

        <nav className="desktop-nav" aria-label="Main navigation">
          {navigation.map((item) => (
            <NavLink key={item.label} to={item.href}>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <Button className="nav-cta" to="/create">
          Create video <ArrowRight size={15} />
        </Button>

        <button
          className="menu-toggle"
          type="button"
          aria-expanded={menuOpen}
          aria-controls="mobile-navigation"
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? <X size={21} /> : <Menu size={21} />}
        </button>
      </div>
      {menuOpen && (
        <nav
          id="mobile-navigation"
          className="mobile-nav"
          aria-label="Mobile navigation"
        >
          {navigation.map((item, index) => (
            <NavLink key={item.label} to={item.href} onClick={closeMenu}>
              <span>0{index + 1}</span>
              {item.label}
              <ArrowDownRight size={20} />
            </NavLink>
          ))}
          <Button to="/create" onClick={closeMenu}>
            Create video <ArrowRight size={16} />
          </Button>
        </nav>
      )}
    </header>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-main">
        <div className="footer-about">
          <BrandLogo />
          <p>One idea. A whole world of story.</p>
        </div>
        {footerGroups.map((group) => (
          <div className="footer-group" key={group.title}>
            <h2>{group.title}</h2>
            {group.links.map((item) => (
              <Link to={item.href} key={item.label}>
                {item.label}
              </Link>
            ))}
          </div>
        ))}
      </div>
      <div className="footer-bottom">
        <span>
          © {new Date().getFullYear()} {brand.name}. Made for stories.
        </span>
        <span className="footer-status">
          <i aria-hidden="true" /> Your creative studio is ready
        </span>
        <a href="mailto:hello@youtubeagent.app">
          Say hello <ArrowUpRightIcon />
        </a>
      </div>
    </footer>
  );
}

function ArrowUpRightIcon() {
  return (
    <ArrowDownRight size={14} className="arrow-up-right" aria-hidden="true" />
  );
}

function CookieBanner() {
  const [visible, setVisible] = useState(
    () => localStorage.getItem("youtubeagent-cookie-choice") === null,
  );

  const choose = (choice: "essential" | "all") => {
    localStorage.setItem("youtubeagent-cookie-choice", choice);
    setVisible(false);
  };

  return (
    <AnimatePresence>
      {visible && (
        <motion.aside
          className="cookie-banner"
          aria-label="Cookie preferences"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 24 }}
        >
          <div>
            <strong>A small note about cookies</strong>
            <p>
              We only save your preference so this notice stays out of your way.
            </p>
          </div>
          <div className="cookie-actions">
            <button
              type="button"
              className="cookie-text-button"
              onClick={() => choose("essential")}
            >
              Essential only
            </button>
            <Button onClick={() => choose("all")}>Sounds good</Button>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function Hero() {
  const [demoOpen, setDemoOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    if (location.hash === "#demo") setDemoOpen(true);
  }, [location.hash]);

  return (
    <section className="hero-section">
      <MediaSlot id="heroLoop" className="ambient-media" />
      <div className="hero-grid lines-bg" aria-hidden="true" />
      <div className="hero-inner content-width">
        <motion.div
          className="hero-copy"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.75, ease: [0.2, 0.65, 0.3, 1] }}
        >
          <div className="hero-kicker">
            <span className="kicker-spark">
              <Sparkles size={13} />
            </span>
            {homeContent.announcement}
          </div>
          <h1>
            {homeContent.heroTitleStart}
            <br />
            <span>{homeContent.heroTitleEnd}</span>
          </h1>
          <p className="hero-description">{homeContent.heroDescription}</p>
          <div className="hero-actions">
            <Button to="/create">
              {homeContent.heroPrimary}
              <ArrowRight size={16} />
            </Button>
            <button
              type="button"
              className="watch-button"
              onClick={() => setDemoOpen(true)}
            >
              <span>
                <Play size={13} fill="currentColor" />
              </span>
              {homeContent.heroSecondary}
            </button>
          </div>
          <div className="hero-proof">
            <span className="proof-avatars">
              <i>Y</i>
              <i>A</i>
              <i>+</i>
            </span>
            <span>{homeContent.heroNote}</span>
          </div>
        </motion.div>

        <motion.div
          className="hero-product"
          initial={{ opacity: 0, y: 32, rotate: 1.5 }}
          animate={{ opacity: 1, y: 0, rotate: 0 }}
          transition={{
            duration: 0.85,
            delay: 0.15,
            ease: [0.2, 0.65, 0.3, 1],
          }}
        >
          <div className="preview-window">
            <div className="preview-topbar">
              <div className="window-lights">
                <i />
                <i />
                <i />
              </div>
              <span>NEW STORY / 01</span>
              <span className="preview-menu-dots">•••</span>
            </div>
            <div className="preview-content">
              <div className="preview-title-row">
                <div>
                  <small>YOUR VIDEO</small>
                  <strong>A quieter kind of magic</strong>
                </div>
                <span className="preview-tag">
                  <i /> IN PROGRESS
                </span>
              </div>
              <div className="preview-scene">
                <div className="preview-image">
                  <div className="preview-landscape" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </div>
                  <div className="scene-title">
                    A little light
                    <br />
                    in the wild
                  </div>
                  <span className="scene-duration">00:18</span>
                </div>
                <div className="preview-timeline">
                  <i />
                  <i />
                  <i />
                  <i />
                  <span />
                </div>
              </div>
              <div className="preview-progress">
                <div className="preview-stage-row">
                  <span>
                    <span className="pulse-dot" /> Creating the shots
                  </span>
                  <span>
                    02 <i>/ 04</i>
                  </span>
                </div>
                <div className="progress-line">
                  <span />
                </div>
                <div className="preview-stages">
                  <span className="stage-complete">
                    <Check size={11} />
                    Story
                  </span>
                  <span className="stage-active">
                    <i />
                    Shots
                  </span>
                  <span>Voice</span>
                  <span>Video</span>
                </div>
              </div>
            </div>
          </div>
          <div className="floating-note note-top">
            <span className="note-icon">
              <Sparkles size={13} />
            </span>
            <span>
              <b>Story first</b>
              <small>Always sounds like you</small>
            </span>
          </div>
          <div className="floating-note note-bottom">
            <span className="note-wave">
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
            <span>
              <b>Voice ready</b>
              <small>Natural narration</small>
            </span>
            <Check className="note-check" size={15} />
          </div>
          <span className="hero-orbit orbit-one" aria-hidden="true" />
          <span className="hero-orbit orbit-two" aria-hidden="true" />
        </motion.div>
        <a className="hero-scroll" href="#how-it-works">
          <span>SCROLL TO EXPLORE</span>
          <ArrowDown size={14} />
        </a>
      </div>
      <Modal
        open={demoOpen}
        onClose={() => setDemoOpen(false)}
        label={`${brand.name} video preview`}
      >
        <VideoPlayer />
      </Modal>
    </section>
  );
}

function HowItWorks() {
  const [activeStep, setActiveStep] = useState(0);
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const section = sectionRef.current;
    if (
      !section ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const triggers = gsap.utils
      .toArray<HTMLElement>(".story-step", section)
      .map((element, index) =>
        ScrollTrigger.create({
          trigger: element,
          start: "top center",
          end: "bottom center",
          onEnter: () => setActiveStep(index),
          onEnterBack: () => setActiveStep(index),
        }),
      );
    return () => triggers.forEach((trigger) => trigger.kill());
  }, []);

  const currentStep = steps[activeStep];
  return (
    <section className="section how-section" id="how-it-works" ref={sectionRef}>
      <div className="content-width">
        <div className="section-heading centered">
          <p className="eyebrow">{homeContent.howEyebrow}</p>
          <h2>{homeContent.howTitle}</h2>
          <p className="section-description">{homeContent.howDescription}</p>
        </div>
        <div className="story-grid">
          <div className="story-list">
            {steps.map((step, index) => (
              <article
                className={`story-step ${activeStep === index ? "is-active" : ""}`}
                key={step.id}
              >
                <span className="story-index">{step.number}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.detail}</p>
                </div>
                <span className="story-check" aria-hidden="true">
                  <Check size={14} />
                </span>
              </article>
            ))}
          </div>
          <div className="story-visual-wrap">
            <div className="story-visual">
              <MediaSlot
                key={currentStep.id}
                id={stepMediaIds[activeStep]}
                className="story-media-slot"
              />
              <div className="story-visual-gradient" />
              <div className="story-caption">
                <span>
                  ACT {currentStep.number} <i>/ 04</i>
                </span>
                <h3>{currentStep.title}</h3>
              </div>
              <div className="story-play-mark">
                <Play size={18} fill="currentColor" />
              </div>
              <div className="story-progress">
                <span
                  style={{
                    width: `${((activeStep + 1) / steps.length) * 100}%`,
                  }}
                />
              </div>
            </div>
            <div className="story-visual-label">
              <span>
                <i /> THE MAKING OF YOUR NEXT VIDEO
              </span>
              <span>{currentStep.number} / 04</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function FeatureGrid({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`feature-grid ${compact ? "feature-grid-compact" : ""}`}>
      {features.map((feature, index) => {
        const Icon = iconMap[feature.icon as keyof typeof iconMap];
        return (
          <motion.div
            key={feature.title}
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.18 }}
            transition={{ duration: 0.45, delay: (index % 3) * 0.08 }}
          >
            <Card
              className={`feature-card feature-${feature.tone} feature-visual-${feature.visual}`}
            >
              <div className="feature-visual" aria-hidden="true">
                {feature.visual === "story" && (
                  <div className="visual-story">
                    <span>THE STORY</span>
                    <i />
                    <i />
                    <i />
                    <i />
                    <small>01 / 04</small>
                  </div>
                )}
                {feature.visual === "frames" && (
                  <div className="visual-frames">
                    <i />
                    <i />
                    <i />
                  </div>
                )}
                {feature.visual === "wave" && (
                  <div className="visual-wave">
                    {Array.from({ length: 28 }, (_, bar) => (
                      <i
                        key={bar}
                        style={
                          {
                            "--bar": `${25 + ((bar * 17) % 70)}%`,
                          } as CSSProperties
                        }
                      />
                    ))}
                  </div>
                )}
                {feature.visual === "control" && (
                  <div className="visual-control">
                    <span>
                      <i /> GENERATING
                    </span>
                    <b>02:14</b>
                    <span className="control-stop">
                      <Pause size={13} fill="currentColor" /> Stop
                    </span>
                  </div>
                )}
                {feature.visual === "assets" && (
                  <div className="visual-assets">
                    <span>
                      <Film size={15} /> final-cut.mp4 <b>MP4</b>
                    </span>
                    <span>
                      <AudioLines size={15} /> narration.mp3 <b>MP3</b>
                    </span>
                    <span>
                      <ImageIcon size={15} /> 8 illustrated shots <b>PNG</b>
                    </span>
                  </div>
                )}
                {feature.visual === "secure" && (
                  <div className="visual-secure">
                    <ShieldCheck size={32} />
                    <span>KEYS STAY SERVER-SIDE</span>
                    <i>● ● ● ● ● ● ● ●</i>
                  </div>
                )}
                <span className="feature-icon">
                  <Icon size={17} strokeWidth={1.8} />
                </span>
              </div>
              <div className="feature-copy">
                <p className="feature-eyebrow">{feature.eyebrow}</p>
                <h3>{feature.title}</h3>
                <p>{feature.copy}</p>
              </div>
            </Card>
          </motion.div>
        );
      })}
    </div>
  );
}

function DemoSection() {
  return (
    <Section
      className="demo-section"
      eyebrow={homeContent.demoEyebrow}
      title={homeContent.demoTitle}
      description={homeContent.demoDescription}
    >
      <VideoPlayer />
      <div className="demo-meta">
        <span>
          <i /> A SHORT FILM, MADE FROM A SIMPLE PROMPT
        </span>
        <span>VIDEO PREVIEW</span>
      </div>
    </Section>
  );
}

function Stats() {
  const [started, setStarted] = useState(false);
  const [counts, setCounts] = useState([0, 0]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setStarted(true);
          observer.disconnect();
        }
      },
      { threshold: 0.45 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!started) return;
    const start = performance.now();
    let frame = 0;
    const animate = (now: number) => {
      const progress = Math.min(1, (now - start) / 850);
      const eased = 1 - (1 - progress) ** 3;
      setCounts([Math.round(4 * eased), Math.round(eased)]);
      if (progress < 1) frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [started]);

  return (
    <div className="stats-row" ref={ref}>
      <div>
        <strong>{String(counts[0]).padStart(2, "0")}</strong>
        <span>CONNECTED CREATIVE STAGES</span>
      </div>
      <div>
        <strong>{String(counts[1]).padStart(2, "0")}</strong>
        <span>IDEA TO GET STARTED</span>
      </div>
      <div>
        <strong>{started ? "MP4" : "···"}</strong>
        <span>ONE FINISHED VIDEO</span>
      </div>
    </div>
  );
}

function CreatorVoices() {
  const [active, setActive] = useState(0);
  return (
    <section className="creator-section">
      <div className="creator-inner content-width">
        <div className="creator-heading">
          <p className="eyebrow">{homeContent.testimonialEyebrow}</p>
          <h2>{homeContent.testimonialTitle}</h2>
          <p>
            Good creative tools give you more room to do the part only you can
            do.
          </p>
          <div className="slider-controls">
            <button
              type="button"
              aria-label="Previous creative note"
              onClick={() => setActive((active + 2) % 3)}
            >
              <ArrowRight size={16} />
            </button>
            <button
              type="button"
              aria-label="Next creative note"
              onClick={() => setActive((active + 1) % 3)}
            >
              <ArrowRight size={16} />
            </button>
            <span>
              0{active + 1} <i>/ 03</i>
            </span>
          </div>
        </div>
        <div className="creator-quote" aria-live="polite">
          <AnimatePresence mode="wait">
            <motion.div
              key={active}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.24 }}
            >
              <span className="quote-mark">“</span>
              <blockquote>{creatorNotes[active].text}</blockquote>
              <div className="quote-author">
                <span className="quote-avatar">
                  <Sparkles size={16} />
                </span>
                <span>
                  <strong>{creatorNotes[active].title}</strong>
                  <small>{creatorNotes[active].tag}</small>
                </span>
                <span className="quote-indicator">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </section>
  );
}

function PricingCards({ compact = false }: { compact?: boolean }) {
  return (
    <div className="pricing-grid">
      {pricing.map((tier) => (
        <Card
          className={`pricing-card ${tier.featured ? "pricing-featured" : ""}`}
          key={tier.name}
        >
          {tier.featured && (
            <span className="popular-tag">
              <Sparkles size={12} /> MOST FLEXIBLE
            </span>
          )}
          <p className="tier-name">{tier.name}</p>
          <p className="tier-price">
            {tier.price}
            <span>{tier.cadence}</span>
          </p>
          <p className="tier-description">{tier.description}</p>
          <Button
            to={tier.href}
            variant={tier.featured ? "primary" : "secondary"}
          >
            {tier.action}
            <ArrowRight size={15} />
          </Button>
          <div className="tier-divider" />
          <p className="tier-includes">INCLUDES</p>
          <ul>
            {tier.features.slice(0, compact ? 2 : undefined).map((item) => (
              <li key={item}>
                <Check size={14} />
                {item}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function FAQPreview() {
  return (
    <Section
      className="faq-preview"
      eyebrow={homeContent.faqEyebrow}
      title={homeContent.faqTitle}
    >
      <Accordion items={faqs.slice(0, 4)} />
      <Link className="text-link faq-link" to="/faq">
        Explore all questions <ArrowRight size={15} />
      </Link>
    </Section>
  );
}

function FinalCTA() {
  return (
    <section className="final-cta">
      <MediaSlot id="ctaLoop" className="ambient-media ambient-cta" />
      <div className="final-cta-inner content-width">
        <p className="eyebrow">{homeContent.finalEyebrow}</p>
        <h2>{homeContent.finalTitle}</h2>
        <p>{homeContent.finalDescription}</p>
        <Button to="/create">
          {homeContent.heroPrimary}
          <ArrowRight size={16} />
        </Button>
      </div>
    </section>
  );
}

function HomePage() {
  return (
    <>
      <Hero />
      <main>
        <section className="tech-section content-width">
          <p>THE CREATIVE STACK</p>
          <Marquee items={technologies} />
        </section>
        <HowItWorks />
        <Section
          className="features-section content-width"
          eyebrow={homeContent.featureEyebrow}
          title={homeContent.featureTitle}
          description={homeContent.featureDescription}
        >
          <FeatureGrid />
        </Section>
        <DemoSection />
        <section className="stats-section content-width">
          <Stats />
        </section>
        <CreatorVoices />
        <Section
          className="pricing-preview content-width"
          eyebrow={homeContent.pricingEyebrow}
          title={homeContent.pricingTitle}
        >
          <PricingCards compact />
        </Section>
        <FAQPreview />
        <section className="home-generator-wrap content-width">
          <div className="home-generator-intro">
            <p className="eyebrow">{homeContent.generatorEyebrow}</p>
            <h2>{homeContent.generatorTitle}</h2>
            <p>{homeContent.generatorDescription}</p>
          </div>
          <Generator compact />
        </section>
      </main>
      <FinalCTA />
    </>
  );
}

function Generator({ compact = false }: { compact?: boolean }) {
  const [idea, setIdea] = useState("");
  const { job, active, submitting, stopping, error, create, stop } = useJob();
  const busy = submitting || active;
  const progress = getProgress(job?.status ?? null, job?.stage ?? null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!idea.trim() || busy) return;
    void create(idea.trim());
  };

  return (
    <div className={`generator-layout ${compact ? "generator-compact" : ""}`}>
      <section className="generator-input glass-card">
        <div className="generator-card-heading">
          <span className="form-step">
            <span>01</span> YOUR IDEA
          </span>
          <span className="secure-note">
            <ShieldCheck size={13} /> PRIVATE BY DESIGN
          </span>
        </div>
        <form onSubmit={submit}>
          <label htmlFor={`video-idea-${compact ? "home" : "create"}`}>
            Your idea
          </label>
          <textarea
            id={`video-idea-${compact ? "home" : "create"}`}
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            placeholder="A tiny lighthouse keeper discovers a message hidden in the stars..."
            maxLength={12000}
            rows={compact ? 4 : 7}
            disabled={busy}
            required
            aria-describedby="idea-hint idea-count"
          />
          <div className="input-meta">
            <span id="idea-hint">
              A simple prompt is all it takes to begin.
            </span>
            <span id="idea-count">
              {idea.length.toLocaleString()}
              <i> / 12,000</i>
            </span>
          </div>
          <div className="generator-actions">
            <Button type="submit" disabled={busy || !idea.trim()}>
              {submitting
                ? "Starting your story"
                : busy
                  ? "Creating your video"
                  : "Create video"}
              {busy ? (
                <span className="button-spinner" />
              ) : (
                <ArrowRight size={16} />
              )}
            </Button>
            {active && job && (
              <button
                className="stop-job"
                type="button"
                onClick={() => void stop()}
                disabled={stopping || job.status === "cancelling"}
              >
                {stopping || job.status === "cancelling"
                  ? "Stopping..."
                  : "Stop"}
              </button>
            )}
          </div>
        </form>
        {error && (
          <p className="inline-alert" role="alert">
            {error}
          </p>
        )}
        {!job && !error && (
          <p className="generator-privacy">
            <ShieldCheck size={14} /> Your provider keys never leave the server.
          </p>
        )}
      </section>

      <section className="generator-progress glass-card">
        <div className="generator-card-heading">
          <span className="form-step">
            <span>02</span> THE CREATIVE PROCESS
          </span>
          <span className={`job-badge ${job?.status ?? "idle"}`}>
            <i />
            {statusLabel(job?.status ?? null)}
          </span>
        </div>
        <div className="progress-heading">
          <div>
            <h2>
              {job?.status === "complete"
                ? "Your video is ready."
                : job
                  ? "Your story is taking shape."
                  : "A little magic, in the making."}
            </h2>
            <p role="status">
              {job?.message ?? "Four thoughtful steps. One finished video."}
            </p>
          </div>
          <span>
            {progress}
            <i>%</i>
          </span>
        </div>
        <div
          className="main-progress"
          role="progressbar"
          aria-label="Video generation progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
        <Stepper status={job?.status ?? null} stage={job?.stage ?? null} />
        {!job && !error && (
          <div className="progress-empty">
            {submitting ? (
              <span className="button-spinner" />
            ) : (
              <WandSparkles size={16} />
            )}
            <span>
              {submitting
                ? "Starting your video..."
                : "Your process will appear here as soon as you begin."}
            </span>
          </div>
        )}
        {job?.status === "failed" && job.error && (
          <p className="inline-alert" role="alert">
            {job.error}
          </p>
        )}
        {job?.status === "cancelled" && (
          <p className="job-note" role="status">
            This generation was stopped. Your next idea is always welcome.
          </p>
        )}
      </section>

      {job?.status === "complete" && job.result && (
        <section className="generator-result glass-card">
          <div className="result-heading">
            <div>
              <span className="form-step">
                <span>03</span> YOUR FINISHED VIDEO
              </span>
              <h2>Made from your idea.</h2>
            </div>
            <a
              className="button button-primary result-download"
              href={job.result.videoUrl}
              download
            >
              Download MP4 <Download size={15} />
            </a>
          </div>
          <video
            className="generated-video"
            controls
            preload="metadata"
            playsInline
          >
            <source src={job.result.videoUrl} type="video/mp4" />
            Your browser does not support video playback.
          </video>
          <div className="asset-links">
            <a href={job.result.dialogueUrl} target="_blank" rel="noreferrer">
              Read your script <ArrowDownRight size={14} />
            </a>
            <a href={job.result.audioUrl} download>
              Download narration <Download size={14} />
            </a>
            <span>
              Saved in <code>{job.result.outputFolder}</code>
            </span>
          </div>
          {job.result.imageUrls.length > 0 && (
            <div className="generated-gallery">
              <div className="gallery-heading">
                <h3>Your illustrated shots</h3>
                <span>{job.result.imageUrls.length} IMAGES</span>
              </div>
              <div className="gallery-grid">
                {job.result.imageUrls.map((url, index) => (
                  <a href={url} target="_blank" rel="noreferrer" key={url}>
                    <img
                      src={url}
                      alt={`Generated story illustration ${index + 1}`}
                      loading="lazy"
                    />
                    <span>SCENE {String(index + 1).padStart(2, "0")}</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function getProgress(status: JobStatus | null, stage: JobStage | null) {
  if (status === "complete") return 100;
  if (!status || status === "cancelled" || status === "failed") return 0;
  const index = steps.findIndex((step) => step.id === stage);
  return index < 0 ? 0 : Math.round(((index + 1) / steps.length) * 88);
}

function statusLabel(status: JobStatus | null) {
  if (status === "complete") return "READY";
  if (status === "failed") return "NEEDS ATTENTION";
  if (status === "cancelled") return "STOPPED";
  if (status === "cancelling") return "STOPPING";
  if (status === "queued" || status === "running") return "IN PROGRESS";
  return "READY WHEN YOU ARE";
}

function CreatePage() {
  return (
    <main className="page-main content-width create-page">
      <div className="page-heading create-heading">
        <p className="eyebrow">
          <span className="eyebrow-line" /> THE VIDEO WORKSPACE
        </p>
        <h1>
          Make something
          <br />
          <span>worth watching.</span>
        </h1>
        <p>
          Bring us an idea. Stay close while it becomes a story, a voice, and a
          video.
        </p>
        <div className="workspace-note">
          <span>
            <i /> ALL SYSTEMS READY
          </span>
          <span>
            <Zap size={13} /> YOUR CREATIVE FLOW, IN REAL TIME
          </span>
        </div>
      </div>
      <Generator />
      <div className="create-footnote">
        <span>
          <Check size={14} /> Your idea stays in your configured workspace.
        </span>
        <span>12,000 character limit</span>
      </div>
    </main>
  );
}

function FeaturesPage() {
  return (
    <main className="page-main content-width">
      <div className="page-heading">
        <p className="eyebrow">
          <span className="eyebrow-line" /> MORE STORY. LESS BUSYWORK.
        </p>
        <h1>
          All the pieces.
          <br />
          <span>One creative flow.</span>
        </h1>
        <p>
          A connected toolkit for getting from your first thought to a finished
          video, without losing the joy in the making.
        </p>
      </div>
      <FeatureGrid />
      <div className="feature-cta">
        <div>
          <p className="eyebrow">YOUR NEXT STEP</p>
          <h2>Bring your next idea to life.</h2>
        </div>
        <Button to="/create">
          Create a video <ArrowRight size={16} />
        </Button>
      </div>
    </main>
  );
}

function PricingPage() {
  return (
    <main className="page-main content-width">
      <div className="page-heading centered">
        <p className="eyebrow">
          <span className="eyebrow-line" /> SIMPLE BY DESIGN
        </p>
        <h1>
          More creating.
          <br />
          <span>Less counting.</span>
        </h1>
        <p>
          Start making videos with the complete workflow. No surprise paywall
          halfway through your story.
        </p>
      </div>
      <PricingCards />
      <p className="pricing-footnote">
        <ShieldCheck size={14} /> {brand.name} is in preview. Any third-party
        service fees are set by your provider.
      </p>
    </main>
  );
}

function FAQPage() {
  return (
    <main className="page-main content-width faq-page">
      <div className="page-heading">
        <p className="eyebrow">
          <span className="eyebrow-line" /> THE THINGS YOU’RE WONDERING
        </p>
        <h1>
          A few good
          <br />
          <span>questions.</span>
        </h1>
        <p>Everything you need to know before your first story takes shape.</p>
      </div>
      <Accordion items={faqs} />
      <div className="faq-contact">
        <span>Still curious?</span>
        <Link to="/contact">
          Talk to us <ArrowRight size={15} />
        </Link>
      </div>
    </main>
  );
}

function ContactPage() {
  const [submitted, setSubmitted] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const subject = encodeURIComponent(`A note for ${brand.name} from ${name}`);
    const body = encodeURIComponent(`${message}\n\nFrom ${name} (${email})`);
    window.location.href = `mailto:${contactCopy.email}?subject=${subject}&body=${body}`;
    setSubmitted(true);
  };

  return (
    <main className="page-main content-width contact-page">
      <div className="page-heading">
        <p className="eyebrow">
          <span className="eyebrow-line" /> OPEN INVITATION
        </p>
        <h1>
          {contactCopy.title.split(" ").slice(0, 2).join(" ")}
          <br />
          <span>{contactCopy.title.split(" ").slice(2).join(" ")}</span>
        </h1>
        <p>{contactCopy.description}</p>
      </div>
      <div className="contact-grid">
        <Card className="contact-card">
          <form onSubmit={submit}>
            <label htmlFor="contact-name">Your name</label>
            <input
              id="contact-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="name"
              required
            />
            <label htmlFor="contact-email">Email address</label>
            <input
              id="contact-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
            />
            <label htmlFor="contact-message">What’s on your mind?</label>
            <textarea
              id="contact-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              rows={5}
              required
            />
            <Button type="submit">
              Open your email app <ArrowRight size={15} />
            </Button>
            {submitted && (
              <p className="contact-success" role="status">
                Your email app should be ready with your message.
              </p>
            )}
          </form>
        </Card>
        <div className="contact-aside">
          <Sparkles size={22} />
          <h2>We love a good story.</h2>
          <p>
            Share a question, a bug you spotted, or the thing you wish your
            creative tools could do.
          </p>
          <a href={`mailto:${contactCopy.email}`}>
            {contactCopy.email}
            <ArrowDownRight size={14} />
          </a>
        </div>
      </div>
    </main>
  );
}

function LegalPage({ type }: { type: "privacy" | "terms" }) {
  const copy = legalCopy[type];
  return (
    <main className="page-main content-width legal-page">
      <div className="page-heading">
        <p className="eyebrow">
          <span className="eyebrow-line" /> THE IMPORTANT DETAILS
        </p>
        <h1>{copy.title}</h1>
        <p>{copy.updated}</p>
      </div>
      <div className="legal-body">
        {copy.sections.map((section) => (
          <section key={section.heading}>
            <h2>{section.heading}</h2>
            <p>{section.body}</p>
          </section>
        ))}
        <p className="legal-disclaimer">
          This page is general product information and is not legal advice. The
          operator of the deployment you use is responsible for its policies.
        </p>
      </div>
    </main>
  );
}

function NotFound() {
  return (
    <main className="page-main content-width not-found">
      <p className="eyebrow">A LITTLE OFF SCRIPT</p>
      <h1>
        This page isn’t
        <br />
        <span>in the story yet.</span>
      </h1>
      <Button to="/">
        Back to the beginning <ArrowRight size={16} />
      </Button>
    </main>
  );
}

function PageLoader() {
  const [phase, setPhase] = useState<"visible" | "exiting" | "hidden">(
    "visible",
  );
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    const exitTimer = window.setTimeout(
      () => setPhase("exiting"),
      prefersReducedMotion ? 50 : 1350,
    );
    const hideTimer = window.setTimeout(
      () => setPhase("hidden"),
      prefersReducedMotion ? 70 : 1680,
    );
    return () => {
      window.clearTimeout(exitTimer);
      window.clearTimeout(hideTimer);
    };
  }, [prefersReducedMotion]);

  if (phase === "hidden") return null;

  return (
    <div
      className={`utube-agent-loader-overlay ${phase === "exiting" ? "is-exiting" : ""}`}
    >
      <UtubeAgentLoader
        size="large"
        background="transparent"
        fullScreen
        text="Initializing AI Agents..."
      />
    </div>
  );
}

export default function App() {
  usePageEffects();
  const prefersReducedMotion = useReducedMotion();
  return (
    <MotionConfig reducedMotion={prefersReducedMotion ? "always" : "never"}>
      <PageLoader />
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Header />
      <div id="main-content">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/create" element={<CreatePage />} />
          <Route path="/features" element={<FeaturesPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/faq" element={<FAQPage />} />
          <Route path="/contact" element={<ContactPage />} />
          <Route path="/privacy" element={<LegalPage type="privacy" />} />
          <Route path="/terms" element={<LegalPage type="terms" />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </div>
      <Footer />
      <CookieBanner />
    </MotionConfig>
  );
}
