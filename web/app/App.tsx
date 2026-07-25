import {
  AlertCircle,
  AudioLines,
  BookOpenText,
  Check,
  ChevronRight,
  CircleCheck,
  Clock3,
  Download,
  FileAudio,
  FileText,
  Gauge,
  Headphones,
  Library,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UploadCloud,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from "react";

type ConnectionState = "checking" | "ready" | "offline";
type JobStatus = "queued" | "running" | "completed" | "failed";
type OutputFormat = "mp3" | "m4a" | "wav";

type Capabilities = {
  needs_api_key?: boolean;
  supports_rate?: boolean;
  supports_pitch?: boolean;
  notes?: string;
};

type Provider = {
  id: string;
  label: string;
  available: boolean;
  capabilities: Capabilities;
};

type Voice = {
  id: string;
  label: string;
  gender?: string;
  locale?: string;
  provider?: string;
};

type JobResult = {
  filename?: string;
  format?: string;
  size?: number;
  duration?: number | null;
  chunks?: number;
  total_chars?: number;
  download_url?: string;
};

type Rule = {
  from: string;
  to: string;
};

type WordMark = {
  text: string;
  start: number;
  end: number;
};

type JobSettings = {
  provider?: string;
  voice?: string;
  rate?: number;
  pitch?: number;
  output_format?: string;
  rules?: Rule[];
};

type Job = {
  id: string;
  status: JobStatus;
  stage?: string;
  progress?: number;
  message?: string;
  filename?: string;
  created_at?: string;
  updated_at?: string;
  settings?: JobSettings;
  result?: JobResult | null;
  error?: string | null;
};

type AppView = "studio" | "library";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "In progress",
  completed: "Ready",
  failed: "Failed",
};

const rawApiBase =
  (
    import.meta as ImportMeta & {
      readonly env: { readonly VITE_API_BASE_URL?: string };
    }
  ).env.VITE_API_BASE_URL ?? "";
const API_BASE_URL = String(rawApiBase).replace(/\/$/, "");
const MAX_FILE_SIZE = 100 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [".docx", ".pdf", ".txt", ".md"];
const PREVIEW_TEXT =
  "Settle in and listen. This is how your document will sound as a beautifully narrated audiobook.";

const providerDescriptions: Record<string, string> = {
  edge: "Natural cloud voices with broad language coverage.",
  piper: "Private, lightweight speech generated entirely offline.",
  kokoro: "Expressive, high-quality narration generated locally.",
  apple: "Built-in macOS voices for fast offline narration.",
  openai: "Premium, expressive cloud narration with an API key.",
};

const stageLabels: Record<string, string> = {
  queued: "Waiting to start",
  extracting: "Reading your document",
  cleaning: "Preparing the narration",
  chunking: "Structuring the chapters",
  synthesizing: "Recording the narration",
  assembling: "Finishing your audiobook",
  completed: "Audiobook ready",
  failed: "Generation failed",
};

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

function formatBytes(bytes?: number): string {
  if (bytes === undefined || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
}

function formatDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) {
    return "—";
  }
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remaining = rounded % 60;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function formatTimestamp(iso?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function describeNarrator(settings?: JobSettings): string {
  if (!settings) return "Narrated audiobook";
  const parts = [settings.provider, settings.voice].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Narrated audiobook";
}

function extensionFor(file: File): string {
  const dotIndex = file.name.lastIndexOf(".");
  return dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : "";
}

function parseRules(value: string): Rule[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const separator = line.includes("=>") ? "=>" : "=";
      const separatorIndex = line.indexOf(separator);
      if (separatorIndex < 1) return null;
      const from = line.slice(0, separatorIndex).trim();
      const to = line.slice(separatorIndex + separator.length).trim();
      return from ? { from, to } : null;
    })
    .filter((rule): rule is Rule => rule !== null);
}

// When a provider gives no real word timings, approximate them by distributing
// the known audio duration across words weighted by length. Good enough to guide
// the eye; edge-tts (the default) supplies exact marks and skips this entirely.
function estimateMarks(words: string[], duration: number): WordMark[] {
  if (!words.length || !Number.isFinite(duration) || duration <= 0) return [];
  const weights = words.map((word) => word.length + 1);
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  let elapsed = 0;
  return words.map((word, index) => {
    const span = (weights[index] / total) * duration;
    const mark = { text: word, start: elapsed, end: elapsed + span };
    elapsed += span;
    return mark;
  });
}

// Preferred default narrators, best first. en-US-AndrewNeural matches the engine
// default (narrate.DEFAULT_VOICE); the rest are natural-sounding en-US fallbacks.
const PREFERRED_VOICE_IDS = [
  "en-US-AndrewNeural",
  "en-US-AvaNeural",
  "en-US-EmmaNeural",
  "en-US-BrianNeural",
  "en-US-JennyNeural",
  "en-US-GuyNeural",
];

function isEnglish(voice: Voice): boolean {
  return (voice.locale || "").toLowerCase().replace("_", "-").startsWith("en");
}

// Choose a sensible default instead of the alphabetically-first voice (which was
// an Afrikaans narrator). Prefer a known-good en-US voice, then any US English,
// then any English, then whatever the provider offers first.
function pickDefaultVoice(voices: Voice[]): string {
  for (const id of PREFERRED_VOICE_IDS) {
    if (voices.some((voice) => voice.id === id)) return id;
  }
  const usEnglish = voices.find(
    (voice) => (voice.locale || "").toLowerCase().replace("_", "-") === "en-us",
  );
  if (usEnglish) return usEnglish.id;
  const anyEnglish = voices.find(isEnglish);
  if (anyEnglish) return anyEnglish.id;
  return voices[0]?.id ?? "";
}

function normalizeProgress(progress?: number): number {
  if (!Number.isFinite(progress)) return 0;
  const value = Number(progress);
  return Math.min(100, Math.max(0, value <= 1 ? value * 100 : value));
}

export default function App() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [selectedProvider, setSelectedProvider] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [rate, setRate] = useState(-4);
  const [pitch, setPitch] = useState(0);
  const [format, setFormat] = useState<OutputFormat>("mp3");
  const [rulesText, setRulesText] = useState("");
  const previewAudioRef = useRef<HTMLAudioElement>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [previewMarks, setPreviewMarks] = useState<WordMark[] | null>(null);
  const [estimatedMarks, setEstimatedMarks] = useState<WordMark[] | null>(null);
  const [activeWord, setActiveWord] = useState(-1);
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<AppView>("studio");
  const [libraryJobs, setLibraryJobs] = useState<Job[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState("");

  const activeProvider = useMemo(
    () => providers.find((provider) => provider.id === selectedProvider),
    [providers, selectedProvider],
  );
  const activeVoice = useMemo(
    () => voices.find((voice) => voice.id === selectedVoice),
    [voices, selectedVoice],
  );
  const supportsPitch = activeProvider?.capabilities.supports_pitch !== false;
  const jobActive =
    submitting || job?.status === "queued" || job?.status === "running";
  const progress = normalizeProgress(job?.progress);
  const rules = useMemo(() => parseRules(rulesText), [rulesText]);
  const previewWords = useMemo(
    () => (previewText ? previewText.trim().split(/\s+/) : []),
    [previewText],
  );
  // Prefer real per-word timings when they line up with the displayed words;
  // otherwise fall back to the length-weighted estimate built on metadata load.
  const activeMarks = useMemo<WordMark[] | null>(() => {
    if (previewMarks && previewMarks.length === previewWords.length) {
      return previewMarks;
    }
    return estimatedMarks;
  }, [previewMarks, estimatedMarks, previewWords.length]);

  const checkHealth = useCallback(async () => {
    setConnection("checking");
    try {
      const response = await fetch(apiUrl("/api/health"), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("The narration engine is unavailable.");
      setConnection("ready");
    } catch {
      setConnection("offline");
    }
  }, []);

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true);
    try {
      const response = await fetch(apiUrl("/api/providers"), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Could not load narration providers.");
      const payload = (await response.json()) as { providers?: Provider[] };
      const nextProviders = Array.isArray(payload.providers)
        ? payload.providers
        : [];
      setProviders(nextProviders);
      setSelectedProvider((current) => {
        if (
          current &&
          nextProviders.some(
            (provider) => provider.id === current && provider.available,
          )
        ) {
          return current;
        }
        return (
          nextProviders.find(
            (provider) => provider.id === "edge" && provider.available,
          )?.id ??
          nextProviders.find((provider) => provider.available)?.id ??
          ""
        );
      });
      setConnection("ready");
    } catch (caught) {
      setProviders([]);
      setConnection("offline");
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not connect to the narration engine.",
      );
    } finally {
      setProvidersLoading(false);
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    setLibraryLoading(true);
    setLibraryError("");
    try {
      const response = await fetch(apiUrl("/api/jobs"), {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Could not load your audiobook library.");
      const payload = (await response.json()) as { jobs?: Job[] };
      setLibraryJobs(Array.isArray(payload.jobs) ? payload.jobs : []);
    } catch (caught) {
      setLibraryJobs([]);
      setLibraryError(
        caught instanceof Error
          ? caught.message
          : "Could not load your audiobook library.",
      );
    } finally {
      setLibraryLoading(false);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    void loadProviders();
  }, [checkHealth, loadProviders]);

  // Refresh the library whenever it is opened, and after a job completes so a
  // freshly rendered audiobook appears without a manual reload.
  useEffect(() => {
    if (view === "library") void loadLibrary();
  }, [view, loadLibrary, job?.status]);

  useEffect(() => {
    if (!selectedProvider) {
      setVoices([]);
      setSelectedVoice("");
      return;
    }

    const controller = new AbortController();
    setVoicesLoading(true);
    setPreviewUrl("");
    setPreviewMarks(null);
    setEstimatedMarks(null);
    setActiveWord(-1);
    void fetch(
      apiUrl(`/api/providers/${encodeURIComponent(selectedProvider)}/voices`),
      {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load voices.");
        return (await response.json()) as { voices?: Voice[] };
      })
      .then((payload) => {
        const nextVoices = Array.isArray(payload.voices) ? payload.voices : [];
        setVoices(nextVoices);
        setSelectedVoice((current) =>
          nextVoices.some((voice) => voice.id === current)
            ? current
            : pickDefaultVoice(nextVoices),
        );
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setVoices([]);
        setSelectedVoice("");
        setError(
          caught instanceof Error ? caught.message : "Could not load voices.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setVoicesLoading(false);
      });

    return () => controller.abort();
  }, [selectedProvider]);

  useEffect(() => {
    return () => {
      if (previewUrl.startsWith("blob:")) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const applyJob = useCallback((nextJob: Job) => {
    setJob(nextJob);
    if (nextJob.status === "failed") {
      setError(nextJob.error || nextJob.message || "Audiobook generation failed.");
    }
  }, []);

  useEffect(() => {
    if (!job?.id || (job.status !== "queued" && job.status !== "running")) return;

    let stopped = false;
    const eventSource = new EventSource(
      apiUrl(`/api/jobs/${encodeURIComponent(job.id)}/events`),
    );
    const onEvent = (event: MessageEvent<string>) => {
      try {
        const nextJob = JSON.parse(event.data) as Job;
        applyJob(nextJob);
        if (nextJob.status === "completed" || nextJob.status === "failed") {
          eventSource.close();
        }
      } catch {
        // The polling fallback below remains active for malformed events.
      }
    };
    eventSource.addEventListener("progress", onEvent as EventListener);
    eventSource.addEventListener("complete", onEvent as EventListener);
    eventSource.addEventListener("error", onEvent as EventListener);

    const poll = async () => {
      try {
        const response = await fetch(
          apiUrl(`/api/jobs/${encodeURIComponent(job.id)}`),
          { headers: { Accept: "application/json" } },
        );
        if (!response.ok) return;
        const nextJob = (await response.json()) as Job;
        if (!stopped) applyJob(nextJob);
      } catch {
        // SSE may still be connected; a later poll can recover.
      }
    };

    const interval = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => {
      stopped = true;
      window.clearInterval(interval);
      eventSource.close();
    };
  }, [applyJob, job?.id, job?.status]);

  function chooseFile(nextFile: File | null) {
    setError("");
    setJob(null);
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!ALLOWED_EXTENSIONS.includes(extensionFor(nextFile))) {
      setError("Choose a DOCX, PDF, TXT, or Markdown document.");
      return;
    }
    if (nextFile.size > MAX_FILE_SIZE) {
      setError("That document is larger than 100 MB.");
      return;
    }
    setFile(nextFile);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.target.files?.[0] ?? null);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  }

  async function previewVoice() {
    if (!selectedProvider || !selectedVoice) return;
    setPreviewLoading(true);
    setError("");
    try {
      const response = await fetch(apiUrl("/api/preview"), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider: selectedProvider,
          voice: selectedVoice,
          rate,
          pitch: supportsPitch ? pitch : 0,
          text: PREVIEW_TEXT,
        }),
      });
      const payload = (await response.json().catch(() => null)) as {
        audio_url?: string;
        text?: string;
        marks?: WordMark[] | null;
        detail?: string;
      } | null;
      if (!response.ok || !payload?.audio_url) {
        throw new Error(
          payload?.detail || "Voice preview could not be created.",
        );
      }
      setPreviewText(payload.text || PREVIEW_TEXT);
      setPreviewMarks(payload.marks ?? null);
      setEstimatedMarks(null);
      setActiveWord(-1);
      setPreviewUrl((current) => {
        if (current.startsWith("blob:")) URL.revokeObjectURL(current);
        return apiUrl(payload.audio_url as string);
      });
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Voice preview could not be created.",
      );
    } finally {
      setPreviewLoading(false);
    }
  }

  async function submitJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !selectedProvider || !selectedVoice || jobActive) return;
    setSubmitting(true);
    setError("");
    setJob(null);
    try {
      const body = new FormData();
      body.append("document", file);
      body.append("provider", selectedProvider);
      body.append("voice", selectedVoice);
      body.append("rate", String(rate));
      body.append("pitch", String(supportsPitch ? pitch : 0));
      body.append("output_format", format);
      body.append("max_chars", "3000");
      body.append("keep_chunks", "false");
      if (rules.length) body.append("rules", JSON.stringify(rules));

      const response = await fetch(apiUrl("/api/jobs"), {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
      });
      const payload = (await response.json().catch(() => null)) as
        | (Job & { detail?: string })
        | null;
      if (!response.ok || !payload) {
        throw new Error(payload?.detail || "Could not start audiobook generation.");
      }
      applyJob(payload);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not start audiobook generation.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function resetWorkspace() {
    setFile(null);
    setJob(null);
    setError("");
    setRate(-4);
    setPitch(0);
    setFormat("mp3");
    setRulesText("");
    setPreviewUrl("");
  }

  const downloadUrl = job?.id
    ? apiUrl(
        job.result?.download_url ||
          `/api/jobs/${encodeURIComponent(job.id)}/output`,
      )
    : "";
  const visibleStage =
    job?.stage || (job?.status === "running" ? "synthesizing" : job?.status);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Doc2Audiobook home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">Doc2Audiobook</span>
          <span className="brand-edition">Studio</span>
        </a>

        <nav className="view-nav" aria-label="Views">
          <button
            type="button"
            className={view === "studio" ? "active" : ""}
            onClick={() => setView("studio")}
            aria-pressed={view === "studio"}
          >
            <Sparkles size={15} aria-hidden="true" />
            Studio
          </button>
          <button
            type="button"
            className={view === "library" ? "active" : ""}
            onClick={() => setView("library")}
            aria-pressed={view === "library"}
          >
            <Library size={15} aria-hidden="true" />
            Library
          </button>
        </nav>

        <div className="topbar-actions">
          <span className="local-badge">
            <ShieldCheck size={15} aria-hidden="true" />
            Local workspace
          </span>
          <button
            className={`connection-status connection-${connection}`}
            type="button"
            onClick={() => {
              void checkHealth();
              if (connection === "offline") void loadProviders();
            }}
            aria-label="Check narration engine connection"
          >
            {connection === "ready" ? (
              <Wifi size={15} aria-hidden="true" />
            ) : connection === "offline" ? (
              <WifiOff size={15} aria-hidden="true" />
            ) : (
              <LoaderCircle className="spin" size={15} aria-hidden="true" />
            )}
            <span>
              {connection === "ready"
                ? "Engine ready"
                : connection === "offline"
                  ? "Engine offline"
                  : "Connecting"}
            </span>
          </button>
        </div>
      </header>

      <main>
        {view === "studio" ? (
          <>
        <section className="hero" aria-labelledby="page-title">
          <div>
            <span className="eyebrow">
              <Sparkles size={15} aria-hidden="true" />
              Your personal narration studio
            </span>
            <h1 id="page-title">
              Turn reading into
              <span> listening.</span>
            </h1>
            <p>
              Bring any document, choose a local or cloud voice, and create a
              polished audiobook with files and results kept in your workspace.
            </p>
          </div>
          <div className="hero-note" aria-label="Supported document formats">
            <Headphones size={24} aria-hidden="true" />
            <div>
              <strong>Built for long-form listening</strong>
              <span>DOCX, PDF, TXT, and Markdown</span>
            </div>
          </div>
        </section>

        {error && (
          <div className="alert" role="alert">
            <AlertCircle size={19} aria-hidden="true" />
            <span>{error}</span>
            <button type="button" onClick={() => setError("")} aria-label="Dismiss">
              <X size={17} aria-hidden="true" />
            </button>
          </div>
        )}

        <form className="studio-grid" onSubmit={submitJob}>
          <div className="setup-column">
            <section className="panel step-panel" aria-labelledby="document-heading">
              <div className="panel-heading">
                <span className="step-number">01</span>
                <div>
                  <p className="section-kicker">Source</p>
                  <h2 id="document-heading">Choose your document</h2>
                </div>
                {file && (
                  <span className="complete-badge">
                    <Check size={14} aria-hidden="true" />
                    Added
                  </span>
                )}
              </div>

              {!file ? (
                <div
                  className={`drop-zone ${dragActive ? "drag-active" : ""}`}
                  onDragEnter={(event) => {
                    event.preventDefault();
                    setDragActive(true);
                  }}
                  onDragOver={(event) => event.preventDefault()}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={onDrop}
                  onClick={() => fileInputRef.current?.click()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      fileInputRef.current?.click();
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-label="Upload a document"
                >
                  <span className="upload-icon">
                    <UploadCloud size={26} aria-hidden="true" />
                  </span>
                  <div>
                    <strong>Drop a document here</strong>
                    <span>
                      or <u>browse your files</u>
                    </span>
                  </div>
                  <small>DOCX, PDF, TXT, or MD · up to 100 MB</small>
                </div>
              ) : (
                <div className="file-card">
                  <span className="file-icon">
                    <FileText size={23} aria-hidden="true" />
                  </span>
                  <div className="file-details">
                    <strong title={file.name}>{file.name}</strong>
                    <span>
                      {extensionFor(file).slice(1).toUpperCase()} ·{" "}
                      {formatBytes(file.size)}
                    </span>
                  </div>
                  <button
                    className="icon-button"
                    type="button"
                    onClick={() => chooseFile(null)}
                    disabled={jobActive}
                    aria-label={`Remove ${file.name}`}
                  >
                    <X size={18} aria-hidden="true" />
                  </button>
                </div>
              )}
              <input
                ref={fileInputRef}
                className="visually-hidden"
                type="file"
                accept=".docx,.pdf,.txt,.md"
                onChange={onFileChange}
              />
            </section>

            <section className="panel step-panel" aria-labelledby="narrator-heading">
              <div className="panel-heading">
                <span className="step-number">02</span>
                <div>
                  <p className="section-kicker">Voice</p>
                  <h2 id="narrator-heading">Pick a narrator</h2>
                </div>
              </div>

              <fieldset className="provider-fieldset">
                <legend className="field-label">Speech engine</legend>
                <div className="provider-grid">
                  {providersLoading
                    ? Array.from({ length: 3 }, (_, index) => (
                        <div className="provider-card provider-skeleton" key={index}>
                          <span />
                          <span />
                        </div>
                      ))
                    : providers.map((provider) => {
                        const selected = selectedProvider === provider.id;
                        return (
                          <label
                            className={`provider-card ${selected ? "selected" : ""} ${
                              !provider.available ? "unavailable" : ""
                            }`}
                            key={provider.id}
                          >
                            <input
                              type="radio"
                              name="provider"
                              value={provider.id}
                              checked={selected}
                              disabled={!provider.available || jobActive}
                              onChange={() => setSelectedProvider(provider.id)}
                            />
                            <span className="provider-radio" aria-hidden="true">
                              {selected && <Check size={12} />}
                            </span>
                            <strong>{provider.label}</strong>
                            <small>
                              {provider.available
                                ? providerDescriptions[provider.id] ||
                                  provider.capabilities.notes ||
                                  "Ready for narration."
                                : "Unavailable on this machine"}
                            </small>
                            <span className="provider-meta">
                              {provider.id === "edge" || provider.id === "openai"
                                ? "Cloud"
                                : "Offline"}
                            </span>
                          </label>
                        );
                      })}
                </div>
              </fieldset>

              <div className="voice-control">
                <label htmlFor="voice-select">Narrator voice</label>
                <div className="select-row">
                  <div className="select-wrap">
                    <select
                      id="voice-select"
                      value={selectedVoice}
                      onChange={(event) => {
                        setSelectedVoice(event.target.value);
                        setPreviewUrl("");
                        setPreviewMarks(null);
                        setEstimatedMarks(null);
                        setActiveWord(-1);
                      }}
                      disabled={voicesLoading || !voices.length || jobActive}
                    >
                      {voicesLoading ? (
                        <option>Loading voices…</option>
                      ) : voices.length ? (
                        voices.map((voice) => (
                          <option key={voice.id} value={voice.id}>
                            {voice.label}
                            {voice.locale ? ` · ${voice.locale}` : ""}
                          </option>
                        ))
                      ) : (
                        <option>No voices available</option>
                      )}
                    </select>
                    <ChevronRight size={17} aria-hidden="true" />
                  </div>
                  <button
                    className="secondary-button preview-button"
                    type="button"
                    onClick={() => void previewVoice()}
                    disabled={!selectedVoice || previewLoading || jobActive}
                  >
                    {previewLoading ? (
                      <LoaderCircle className="spin" size={17} aria-hidden="true" />
                    ) : (
                      <Play size={16} fill="currentColor" aria-hidden="true" />
                    )}
                    {previewLoading ? "Preparing" : "Preview"}
                  </button>
                </div>
                {activeVoice && (
                  <p className="field-help">
                    {activeVoice.gender || "Narrator"}
                    {activeVoice.locale ? ` · ${activeVoice.locale}` : ""}
                  </p>
                )}
                {previewUrl && (
                  <div className="preview-playback">
                    <audio
                      ref={previewAudioRef}
                      className="preview-audio"
                      src={previewUrl}
                      controls
                      autoPlay
                      onLoadedMetadata={() => {
                        const audio = previewAudioRef.current;
                        if (!audio) return;
                        if (
                          !previewMarks ||
                          previewMarks.length !== previewWords.length
                        ) {
                          setEstimatedMarks(
                            estimateMarks(previewWords, audio.duration),
                          );
                        }
                      }}
                      onTimeUpdate={() => {
                        const audio = previewAudioRef.current;
                        if (!audio || !activeMarks || !activeMarks.length) return;
                        const current = audio.currentTime;
                        let index = -1;
                        for (let i = 0; i < activeMarks.length; i += 1) {
                          if (current >= activeMarks[i].start) index = i;
                          else break;
                        }
                        setActiveWord(index);
                      }}
                      aria-label={`${activeVoice?.label || "Voice"} preview`}
                    />
                    {previewWords.length > 0 && (
                      <p className="preview-transcript">
                        {previewWords.map((word, index) => (
                          <span
                            key={index}
                            className={`preview-word ${
                              index === activeWord ? "spoken" : ""
                            }`}
                          >
                            {word}
                            {index < previewWords.length - 1 ? " " : ""}
                          </span>
                        ))}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </section>

            <section className="panel step-panel" aria-labelledby="settings-heading">
              <div className="panel-heading">
                <span className="step-number">03</span>
                <div>
                  <p className="section-kicker">Delivery</p>
                  <h2 id="settings-heading">Shape the performance</h2>
                </div>
                <button
                  className="text-button"
                  type="button"
                  onClick={() => {
                    setRate(-4);
                    setPitch(0);
                  }}
                  disabled={jobActive}
                >
                  <RotateCcw size={14} aria-hidden="true" />
                  Reset
                </button>
              </div>

              <div className="slider-grid">
                <label className="slider-control">
                  <span>
                    <span className="slider-label">
                      <Gauge size={17} aria-hidden="true" />
                      Pace
                    </span>
                    <output>{rate > 0 ? `+${rate}%` : `${rate}%`}</output>
                  </span>
                  <input
                    type="range"
                    min="-50"
                    max="100"
                    step="5"
                    value={rate}
                    onChange={(event) => setRate(Number(event.target.value))}
                    disabled={jobActive}
                  />
                  <span className="range-hints">
                    <span>Slower</span>
                    <span>Faster</span>
                  </span>
                </label>

                <label className={`slider-control ${!supportsPitch ? "disabled" : ""}`}>
                  <span>
                    <span className="slider-label">
                      <SlidersHorizontal size={17} aria-hidden="true" />
                      Pitch
                    </span>
                    <output>
                      {supportsPitch
                        ? pitch > 0
                          ? `+${pitch} Hz`
                          : `${pitch} Hz`
                        : "Not supported"}
                    </output>
                  </span>
                  <input
                    type="range"
                    min="-50"
                    max="50"
                    step="5"
                    value={supportsPitch ? pitch : 0}
                    onChange={(event) => setPitch(Number(event.target.value))}
                    disabled={!supportsPitch || jobActive}
                  />
                  <span className="range-hints">
                    <span>Lower</span>
                    <span>Higher</span>
                  </span>
                </label>
              </div>

              <fieldset className="format-fieldset">
                <legend className="field-label">Output format</legend>
                <div className="format-options">
                  {(["mp3", "m4a", "wav"] as OutputFormat[]).map((option) => (
                    <label
                      className={format === option ? "selected" : ""}
                      key={option}
                    >
                      <input
                        type="radio"
                        name="format"
                        value={option}
                        checked={format === option}
                        onChange={() => setFormat(option)}
                        disabled={jobActive}
                      />
                      <strong>{option.toUpperCase()}</strong>
                      <span>
                        {option === "mp3"
                          ? "Universal"
                          : option === "m4a"
                            ? "Compact"
                            : "Uncompressed"}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <details className="rules-panel">
                <summary>
                  <span>
                    <BookOpenText size={17} aria-hidden="true" />
                    Pronunciation & replacement rules
                  </span>
                  <small>
                    {rules.length
                      ? `${rules.length} rule${rules.length === 1 ? "" : "s"}`
                      : "Optional"}
                  </small>
                </summary>
                <div className="rules-content">
                  <label htmlFor="rules">
                    Add one replacement per line using{" "}
                    <code>original = spoken version</code>.
                  </label>
                  <textarea
                    id="rules"
                    value={rulesText}
                    onChange={(event) => setRulesText(event.target.value)}
                    placeholder={"Doc2Audiobook = Doc to Audiobook\nAPI = A P I"}
                    rows={4}
                    disabled={jobActive}
                  />
                  {rulesText.trim() && !rules.length && (
                    <span className="rules-warning">
                      Add an equals sign between the written and spoken text.
                    </span>
                  )}
                </div>
              </details>
            </section>
          </div>

          <aside className="output-column" aria-label="Audiobook generation">
            <div className={`output-card ${job?.status ? `status-${job.status}` : ""}`}>
              {!job ? (
                <>
                  <div className="output-visual" aria-hidden="true">
                    <div className="disc">
                      <div className="disc-ring" />
                      <Headphones size={34} />
                    </div>
                    <div className="sound-bars">
                      {Array.from({ length: 16 }, (_, index) => (
                        <span key={index} />
                      ))}
                    </div>
                  </div>
                  <div className="output-copy">
                    <span className="output-kicker">Your audiobook</span>
                    <h2>Ready when you are.</h2>
                    <p>
                      Add a document and choose your narrator. We’ll handle the
                      pacing, production, and final audio file.
                    </p>
                  </div>

                  <div className="selection-summary">
                    <div>
                      <FileText size={17} aria-hidden="true" />
                      <span>Document</span>
                      <strong title={file?.name}>
                        {file ? file.name : "Not selected"}
                      </strong>
                    </div>
                    <div>
                      <AudioLines size={17} aria-hidden="true" />
                      <span>Narrator</span>
                      <strong>{activeVoice?.label || "Not selected"}</strong>
                    </div>
                    <div>
                      <FileAudio size={17} aria-hidden="true" />
                      <span>Format</span>
                      <strong>{format.toUpperCase()}</strong>
                    </div>
                  </div>

                  <button
                    className="primary-button"
                    type="submit"
                    disabled={
                      !file ||
                      !selectedProvider ||
                      !selectedVoice ||
                      connection !== "ready" ||
                      jobActive
                    }
                  >
                    {submitting ? (
                      <LoaderCircle className="spin" size={19} aria-hidden="true" />
                    ) : (
                      <Sparkles size={18} aria-hidden="true" />
                    )}
                    {submitting ? "Starting…" : "Create audiobook"}
                    {!submitting && <ChevronRight size={18} aria-hidden="true" />}
                  </button>
                  <p className="privacy-note">
                    <ShieldCheck size={14} aria-hidden="true" />
                    Files and results stay local; online providers receive
                    narration text.
                  </p>
                </>
              ) : job.status === "completed" ? (
                <div className="result-state">
                  <span className="success-icon">
                    <CircleCheck size={32} aria-hidden="true" />
                  </span>
                  <span className="output-kicker">Narration complete</span>
                  <h2>Your audiobook is ready.</h2>
                  <p className="result-filename">
                    {job.result?.filename || job.filename || file?.name}
                  </p>

                  <audio
                    className="result-audio"
                    src={downloadUrl}
                    controls
                    preload="metadata"
                    aria-label="Generated audiobook"
                  />

                  <div className="result-metrics">
                    <div>
                      <span>Duration</span>
                      <strong>{formatDuration(job.result?.duration)}</strong>
                    </div>
                    <div>
                      <span>File size</span>
                      <strong>{formatBytes(job.result?.size)}</strong>
                    </div>
                    <div>
                      <span>Format</span>
                      <strong>
                        {(job.result?.format || format).toUpperCase()}
                      </strong>
                    </div>
                  </div>

                  <a className="primary-button download-button" href={downloadUrl}>
                    <Download size={19} aria-hidden="true" />
                    Download audiobook
                  </a>
                  <button
                    className="secondary-button full-width"
                    type="button"
                    onClick={resetWorkspace}
                  >
                    <RefreshCw size={16} aria-hidden="true" />
                    Create another
                  </button>
                </div>
              ) : job.status === "failed" ? (
                <div className="failed-state">
                  <span className="failed-icon">
                    <AlertCircle size={30} aria-hidden="true" />
                  </span>
                  <span className="output-kicker">Generation stopped</span>
                  <h2>Something interrupted the narration.</h2>
                  <p>
                    {job.error ||
                      job.message ||
                      "Please check your settings and try again."}
                  </p>
                  <button
                    className="primary-button"
                    type="button"
                    onClick={() => setJob(null)}
                  >
                    <RefreshCw size={17} aria-hidden="true" />
                    Try again
                  </button>
                </div>
              ) : (
                <div className="progress-state" aria-live="polite">
                  <span className="progress-icon">
                    <AudioLines size={30} aria-hidden="true" />
                  </span>
                  <span className="output-kicker">Creating your audiobook</span>
                  <h2>
                    {stageLabels[visibleStage || "queued"] ||
                      job.message ||
                      "Working…"}
                  </h2>
                  <p>
                    {job.message ||
                      "Your document is being carefully prepared and narrated."}
                  </p>

                  <div className="progress-readout">
                    <div>
                      <span>{Math.round(progress)}%</span>
                      <small>
                        {job.status === "queued" ? "Queued" : "In progress"}
                      </small>
                    </div>
                    <div
                      className="progress-track"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(progress)}
                    >
                      <span style={{ width: `${Math.max(progress, 3)}%` }} />
                    </div>
                  </div>

                  <ol className="progress-steps">
                    {["extracting", "synthesizing", "assembling"].map(
                      (stage, index) => {
                        const order: Record<string, number> = {
                          queued: -1,
                          extracting: 0,
                          cleaning: 0,
                          chunking: 0,
                          synthesizing: 1,
                          assembling: 2,
                        };
                        const currentOrder = order[visibleStage || "queued"] ?? -1;
                        const done = currentOrder > index;
                        const current = currentOrder === index;
                        return (
                          <li
                            className={done ? "done" : current ? "current" : ""}
                            key={stage}
                          >
                            <span>
                              {done ? (
                                <Check size={13} aria-hidden="true" />
                              ) : (
                                index + 1
                              )}
                            </span>
                            {stage === "extracting"
                              ? "Read"
                              : stage === "synthesizing"
                                ? "Narrate"
                                : "Assemble"}
                          </li>
                        );
                      },
                    )}
                  </ol>
                  <p className="time-note">
                    <Clock3 size={14} aria-hidden="true" />
                    Long documents may take a while. You can leave this tab open.
                  </p>
                </div>
              )}
            </div>
          </aside>
        </form>
          </>
        ) : (
          <section className="library-view" aria-labelledby="library-title">
            <div className="library-head">
              <div>
                <span className="eyebrow">
                  <Library size={15} aria-hidden="true" />
                  Your library
                </span>
                <h1 id="library-title" className="library-title">
                  Audiobooks
                </h1>
                <p>
                  Every narration created on this machine. Files stay local —
                  reopen, play, or download them anytime.
                </p>
              </div>
              <button
                className="secondary-button"
                type="button"
                onClick={() => void loadLibrary()}
                disabled={libraryLoading}
              >
                {libraryLoading ? (
                  <LoaderCircle className="spin" size={16} aria-hidden="true" />
                ) : (
                  <RefreshCw size={16} aria-hidden="true" />
                )}
                Refresh
              </button>
            </div>

            {libraryError && (
              <div className="alert" role="alert">
                <AlertCircle size={19} aria-hidden="true" />
                <span>{libraryError}</span>
                <button
                  type="button"
                  onClick={() => setLibraryError("")}
                  aria-label="Dismiss"
                >
                  <X size={17} aria-hidden="true" />
                </button>
              </div>
            )}

            {libraryLoading && !libraryJobs.length ? (
              <div className="library-empty">
                <LoaderCircle className="spin" size={26} aria-hidden="true" />
                <strong>Loading your audiobooks…</strong>
              </div>
            ) : !libraryJobs.length ? (
              <div className="library-empty">
                <span className="library-empty-icon">
                  <BookOpenText size={26} aria-hidden="true" />
                </span>
                <strong>No audiobooks yet</strong>
                <span>Head to the Studio to create your first narration.</span>
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => setView("studio")}
                >
                  <Sparkles size={17} aria-hidden="true" />
                  Open the Studio
                </button>
              </div>
            ) : (
              <ul className="library-list">
                {libraryJobs.map((entry) => {
                  const entryDownloadUrl = apiUrl(
                    entry.result?.download_url ||
                      `/api/jobs/${encodeURIComponent(entry.id)}/output`,
                  );
                  const title =
                    entry.result?.filename || entry.filename || "Audiobook";
                  return (
                    <li
                      className={`library-card status-${entry.status}`}
                      key={entry.id}
                    >
                      <div className="library-card-main">
                        <span className="library-card-icon">
                          <FileAudio size={20} aria-hidden="true" />
                        </span>
                        <div className="library-card-info">
                          <strong title={title}>{title}</strong>
                          <span className="library-card-meta">
                            {describeNarrator(entry.settings)}
                            {entry.created_at
                              ? ` · ${formatTimestamp(entry.created_at)}`
                              : ""}
                          </span>
                        </div>
                        <span className={`status-pill status-pill-${entry.status}`}>
                          {STATUS_LABELS[entry.status]}
                        </span>
                      </div>

                      {entry.status === "completed" ? (
                        <>
                          <div className="library-card-metrics">
                            <div>
                              <span>Duration</span>
                              <strong>
                                {formatDuration(entry.result?.duration)}
                              </strong>
                            </div>
                            <div>
                              <span>Size</span>
                              <strong>{formatBytes(entry.result?.size)}</strong>
                            </div>
                            <div>
                              <span>Format</span>
                              <strong>
                                {(
                                  entry.result?.format ||
                                  entry.settings?.output_format ||
                                  ""
                                ).toUpperCase() || "—"}
                              </strong>
                            </div>
                          </div>
                          <audio
                            src={entryDownloadUrl}
                            controls
                            preload="none"
                            aria-label={`${title} audiobook`}
                          />
                          <div className="library-card-actions">
                            <a
                              className="primary-button"
                              href={entryDownloadUrl}
                              download
                            >
                              <Download size={17} aria-hidden="true" />
                              Download
                            </a>
                          </div>
                        </>
                      ) : entry.status === "failed" ? (
                        <p className="library-card-error">
                          {entry.error ||
                            entry.message ||
                            "This audiobook did not finish. Recreate it in the Studio."}
                        </p>
                      ) : (
                        <p className="library-card-progress">
                          {entry.message || "Still working on this audiobook…"}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        )}
      </main>

      <footer>
        <span>Doc2Audiobook · Local narration studio</span>
        <span>No sign-in. No hosted library. Your files stay yours.</span>
      </footer>
    </div>
  );
}
