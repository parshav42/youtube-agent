export const brand = {
  name: "UTubeAgent",
  description:
    "Turn a video idea into a finished YouTube video. Write the story, create illustrated shots, generate a voiceover, and assemble your MP4.",
};

export const navigation = [
  { label: "How it works", href: "/#how-it-works" },
  { label: "Features", href: "/features" },
  { label: "Pricing", href: "/pricing" },
  { label: "FAQ", href: "/faq" },
];

export const steps = [
  {
    id: "dialogue",
    number: "01",
    title: "Build the story",
    description: "Your idea becomes a thoughtful, ready-to-narrate script.",
    detail:
      "A local language model shapes your idea into a beginning, middle, and end.",
  },
  {
    id: "images",
    number: "02",
    title: "Create the shots",
    description: "Every story beat gets its own illustrated frame.",
    detail:
      "The script is transformed into a series of visuals that give your story a world.",
  },
  {
    id: "speech",
    number: "03",
    title: "Generate the voice",
    description: "Natural narration brings every scene to life.",
    detail:
      "ElevenLabs turns your script into expressive, perfectly timed narration.",
  },
  {
    id: "video",
    number: "04",
    title: "Assemble the video",
    description: "Your finished story, ready to download and share.",
    detail:
      "The final edit brings your images and narration together in a polished MP4.",
  },
] as const;

export const technologies = [
  "Ollama",
  "ElevenLabs",
  "FFmpeg",
  "Supabase",
  "FastAPI",
  "React",
];

export const features = [
  {
    icon: "sparkles",
    eyebrow: "A thoughtful first draft",
    title: "Ideas become stories",
    copy: "Go from a few words to a structured narration with a clear point of view.",
    tone: "violet",
    visual: "story",
  },
  {
    icon: "image",
    eyebrow: "A world of your own",
    title: "Illustrated, scene by scene",
    copy: "Create a visual for every beat, then keep the image files for your next edit.",
    tone: "blue",
    visual: "frames",
  },
  {
    icon: "audio",
    eyebrow: "Made to be heard",
    title: "Natural voiceover",
    copy: "Turn a finished script into a clear, expressive narration.",
    tone: "pink",
    visual: "wave",
  },
  {
    icon: "stop",
    eyebrow: "You stay in control",
    title: "Stop whenever you want",
    copy: "Follow each step as it happens, or cancel a generation in progress.",
    tone: "mint",
    visual: "control",
  },
  {
    icon: "download",
    eyebrow: "Everything comes with you",
    title: "Your creative files",
    copy: "Download the MP4, script, narration, and every generated image.",
    tone: "amber",
    visual: "assets",
  },
  {
    icon: "shield",
    eyebrow: "Your keys stay server-side",
    title: "Secure by design",
    copy: "Provider credentials never reach the browser. Store assets locally or in Supabase.",
    tone: "cyan",
    visual: "secure",
  },
];

export const pricing = [
  {
    name: "Starter",
    price: "Free",
    cadence: "to get started",
    description: "Everything you need to bring your first idea to life.",
    features: [
      "Full video creation flow",
      "Downloadable MP4 and assets",
      "Local job storage",
    ],
    action: "Create your first video",
    href: "/create",
    featured: false,
  },
  {
    name: "Creator",
    price: "Free",
    cadence: "while in preview",
    description: "A little more room for your next big idea.",
    features: [
      "Everything in Starter",
      "Supabase asset storage",
      "Saved generation history",
    ],
    action: "Start creating",
    href: "/create",
    featured: true,
  },
  {
    name: "Studio",
    price: "Let’s talk",
    cadence: "for teams",
    description: "A collaborative workflow for ambitious channels.",
    features: [
      "Everything in Creator",
      "Shared creative workspace",
      "Priority onboarding",
    ],
    action: "Contact us",
    href: "/contact",
    featured: false,
  },
];

export const faqs = [
  {
    question: "What happens when I create a video?",
    answer:
      "UTubeAgent expands your idea into a script, creates illustrated shots, generates a voiceover, and assembles everything into an MP4. You can follow the progress as each step runs.",
  },
  {
    question: "Can I download the individual files?",
    answer:
      "Yes. When your video is ready, you can download the MP4, read the script, save the narration, and open each generated image.",
  },
  {
    question: "Where are my videos stored?",
    answer:
      "By default, generated files are saved in the project’s local output folder. You can optionally configure Supabase storage for persistent jobs and signed asset links.",
  },
  {
    question: "Do I need an API key?",
    answer:
      "Video generation requires an ElevenLabs API key configured on the server. The key stays in your server environment and is never exposed in frontend code.",
  },
  {
    question: "Can I stop a video while it’s generating?",
    answer:
      "Yes. Select Stop from the progress panel. The current generation will stop as soon as the active step can safely respond to cancellation.",
  },
];

export const testimonials = [
  {
    quote:
      "The whole process feels like having a tiny production studio on my desk. I can finally turn a note into something people can watch.",
    name: "Maya R.",
    role: "Independent creator",
    initials: "MR",
  },
  {
    quote:
      "Seeing the story take shape step by step makes the creative part feel accessible, not like another complicated editing workflow.",
    name: "Daniel K.",
    role: "Educator & filmmaker",
    initials: "DK",
  },
  {
    quote:
      "I love getting the individual assets back. The finished video is just the beginning of what I can make with it.",
    name: "Ari S.",
    role: "YouTube creator",
    initials: "AS",
  },
];

export const footerGroups = [
  {
    title: "Explore",
    links: [
      { label: "Features", href: "/features" },
      { label: "Pricing", href: "/pricing" },
      { label: "FAQ", href: "/faq" },
    ],
  },
  {
    title: "Create",
    links: [
      { label: "Start a video", href: "/create" },
      { label: "How it works", href: "/#how-it-works" },
      { label: "Contact", href: "/contact" },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Privacy", href: "/privacy" },
      { label: "Terms", href: "/terms" },
    ],
  },
];

export const legalCopy = {
  privacy: {
    title: "Privacy, without the mystery.",
    updated: "Last updated November 2026",
    sections: [
      {
        heading: "What you provide",
        body: "When you submit a video idea, the text is sent to the configured generation services so the application can create a script, narration, illustrations, and video. Avoid including personal or sensitive information in your idea.",
      },
      {
        heading: "Generated files",
        body: "Generated videos and related files are saved locally by default. If the server is configured to use Supabase, job information and assets may be stored in that project. Asset URLs may be temporary signed links.",
      },
      {
        heading: "Service credentials",
        body: "Provider credentials are held by the server and are not sent to your browser. The service uses them only to perform the generation you request.",
      },
      {
        heading: "Cookies and preferences",
        body: "This site stores your cookie preference in your browser so the notice does not reappear after you make a choice. The application does not use this preference to track you across other websites.",
      },
      {
        heading: "Your choices",
        body: "You can choose not to submit an idea, and you can clear locally stored site preferences from your browser settings. For questions about the specific deployment you are using, contact its operator.",
      },
    ],
  },
  terms: {
    title: "A few things to know.",
    updated: "Last updated November 2026",
    sections: [
      {
        heading: "Using the service",
        body: "Use UTubeAgent lawfully and only with content you have permission to submit. You are responsible for the ideas, scripts, and other material you provide.",
      },
      {
        heading: "Generated content",
        body: "Generated results may require review and editing before publication. You are responsible for checking that your final video, narration, images, and any included material are appropriate for your intended use.",
      },
      {
        heading: "Third-party services",
        body: "Video generation can rely on third-party services configured by the operator, including Ollama, ElevenLabs, FFmpeg, and optionally Supabase. Their availability and terms may apply.",
      },
      {
        heading: "Availability",
        body: "The service is provided as available. Generation may take time or fail because of local resources, network conditions, or provider availability.",
      },
      {
        heading: "Changes",
        body: "The operator may update the application and these terms as the service evolves. Stop using the service if you do not agree with an update.",
      },
    ],
  },
};

export const contactCopy = {
  title: "Let’s make something.",
  description:
    "Have a question, a feature idea, or a story you can’t wait to tell? Send a note and we’ll be in touch.",
  email: "hello@youtubeagent.app",
};

export const homeContent = {
  announcement: "YOUR CREATIVE STUDIO, REIMAGINED",
  heroTitleStart: "Turn any idea",
  heroTitleEnd: "into a YouTube video.",
  heroTitle: "Turn any idea into a YouTube video.",
  heroDescription:
    "From a first spark to a finished film. Your story, illustrated, narrated, and ready to share.",
  heroPrimary: "Create your video",
  heroSecondary: "Watch the film",
  heroNote: "A complete creative workflow, in one place.",
  generatorEyebrow: "YOUR NEXT VIDEO STARTS HERE",
  generatorTitle: "What do you want to make?",
  generatorDescription: "A sentence is enough. Your story can start anywhere.",
  howEyebrow: "A STORY, IN FOUR ACTS",
  howTitle: "From first thought to final frame.",
  howDescription: "One connected process, with you in the loop at every step.",
  featureEyebrow: "THE WHOLE CREATIVE LOOP",
  featureTitle: "A small studio with a big imagination.",
  featureDescription:
    "The tools to get from a good idea to a finished video, without losing the fun along the way.",
  demoEyebrow: "THE FINISHED THING",
  demoTitle: "Imagine what your idea could become.",
  demoDescription:
    "Each video is built from a story, illustrated shots, natural narration, and your final edit.",
  testimonialEyebrow: "MADE FOR THE MAKERS",
  testimonialTitle: "Less setup. More storytelling.",
  pricingEyebrow: "SIMPLE BY DESIGN",
  pricingTitle: "Start with an idea. Stay for the story.",
  faqEyebrow: "GOOD QUESTIONS",
  faqTitle: "A little more about how it works.",
  finalEyebrow: "EVERY GREAT VIDEO STARTS SOMEWHERE",
  finalTitle: "Your next story is waiting.",
  finalDescription: "Bring an idea. Leave with a video.",
};

export const creatorNotes = [
  {
    title: "Keep the spark.",
    text: "Start with what you want to say. Let the process take care of the production steps.",
    tag: "THE IDEA",
  },
  {
    title: "See it take shape.",
    text: "Watch your story turn into scenes, narration, and a complete video, one step at a time.",
    tag: "THE PROCESS",
  },
  {
    title: "Make it your own.",
    text: "Take your finished video and every creative asset into the tools you already love.",
    tag: "THE FINISH",
  },
];

export const mediaContent = {
  comingSoon: "Video coming soon",
  demoPreview: "DEMO PREVIEW",
  demoTitle: "A story is on its way.",
  demoDescription: "This preview slot is ready for your sample film.",
  playPreview: "Play demo preview",
  pausePreview: "Pause demo preview",
  fullscreen: "View demo preview fullscreen",
};

export const pageSeo: Record<string, { title: string; description: string }> = {
  "/": {
    title: "UTubeAgent — Your idea, made watchable",
    description: brand.description,
  },
  "/create": {
    title: "Create a video — UTubeAgent",
    description:
      "Turn a video idea into a script, illustrated scenes, voiceover, and a finished MP4.",
  },
  "/features": {
    title: "Features — UTubeAgent",
    description:
      "Explore the complete creative workflow, from story writing to downloadable video assets.",
  },
  "/pricing": {
    title: "Pricing — UTubeAgent",
    description:
      "Explore UTubeAgent plans and start creating during the product preview.",
  },
  "/faq": {
    title: "Frequently asked questions — UTubeAgent",
    description:
      "Learn how UTubeAgent video generation, storage, and cancellation work.",
  },
  "/contact": {
    title: "Contact — UTubeAgent",
    description:
      "Get in touch with UTubeAgent about your questions, ideas, or feedback.",
  },
  "/privacy": {
    title: "Privacy — UTubeAgent",
    description:
      "Read about video ideas, generated assets, and service credentials in UTubeAgent.",
  },
  "/terms": {
    title: "Terms — UTubeAgent",
    description: "Review the terms for using UTubeAgent and generated content.",
  },
};
