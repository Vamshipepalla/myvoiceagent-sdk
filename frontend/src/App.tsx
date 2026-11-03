import {
  Activity,
  ArrowUpRight,
  AudioLines,
  Bot,
  ChevronDown,
  CircleHelp,
  Headphones,
  LayoutDashboard,
  Mic,
  MoreHorizontal,
  Phone,
  Radio,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import {
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
  type RemoteTrackPublication,
  type RemoteParticipant,
} from "livekit-client";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const API = "http://127.0.0.1:8000/api";

type Agent = {
  name: string;
  display_name: string;
  directions: string[];
  models: { stt: string; llm: string; tts: string };
};
type Call = {
  id: string;
  agent_name: string;
  direction: string;
  started_at: string;
  ended_at: string | null;
  transcript_available: boolean;
};
type Metric = {
  key: string;
  label: string;
  count: number;
  p50_ms: number;
  p90_ms: number;
  mean_ms: number;
};
type TranscriptLine = {
  id: string;
  role: "user" | "agent";
  text: string;
  final: boolean;
  created_at?: number | null;
};
type Page = "overview" | "agents" | "transcripts" | "metrics" | "call";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(call: Call) {
  if (!call.ended_at) return "In progress";
  const seconds = Math.max(
    0,
    Math.round(
      (new Date(call.ended_at).getTime() - new Date(call.started_at).getTime()) /
        1000,
    ),
  );
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function initials(name: string) {
  return name.slice(0, 2).toUpperCase();
}

function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [page, setPage] = useState<Page>("overview");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [callState, setCallState] = useState<
    "idle" | "connecting" | "live" | "ending"
  >("idle");
  const [callStartedAt, setCallStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [callNotice, setCallNotice] = useState("");
  const [transcripts, setTranscripts] = useState<TranscriptLine[]>([]);
  const [playbackError, setPlaybackError] = useState("");
  const [agentConnected, setAgentConnected] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const callIdRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [agentData, callData, metricData] = await Promise.all([
        apiRequest<Agent[]>("/agents"),
        apiRequest<Call[]>("/calls"),
        apiRequest<Metric[]>(
          `/metrics${agentFilter ? `?agent_name=${encodeURIComponent(agentFilter)}` : ""}`,
        ),
      ]);
      setAgents(agentData);
      setCalls(callData);
      setMetrics(metricData);
      setLoadError("");
    } catch (error) {
      setLoadError(
        error instanceof Error
          ? `${error.message}. Start the API with “myagentdashboard”.`
          : "Could not load dashboard data.",
      );
    } finally {
      setIsRefreshing(false);
    }
  }, [agentFilter]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!callStartedAt || callState !== "live") return;
    const tick = () =>
      setElapsed(Math.floor((Date.now() - callStartedAt) / 1000));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [callStartedAt, callState]);

  useEffect(
    () => () => {
      roomRef.current?.disconnect();
    },
    [],
  );

  const currentAgent = agents.find((agent) => agent.name === selectedAgent) ?? null;
  const visibleCalls = useMemo(
    () =>
      agentFilter
        ? calls.filter((call) => call.agent_name === agentFilter)
        : calls,
    [agentFilter, calls],
  );
  const totalCalls = visibleCalls.length;
  const e2eMetric = metrics.find((metric) => metric.key === "e2e_latency");
  const overviewCards = [
    {
      label: "Total calls",
      value: totalCalls.toLocaleString(),
      note: "Across all agent sessions",
      icon: Phone,
      accent: "mint",
    },
    {
      label: "Median response",
      value: e2eMetric ? `${e2eMetric.p50_ms} ms` : "—",
      note: e2eMetric
        ? `P90 ${e2eMetric.p90_ms} ms · ${e2eMetric.count} samples`
        : "Waiting for conversation data",
      icon: Activity,
      accent: "blue",
    },
    {
      label: "Transcripts",
      value: visibleCalls.filter((call) => call.transcript_available).length.toString(),
      note: "Call turns stored locally",
      icon: AudioLines,
      accent: "purple",
    },
    {
      label: "Agents",
      value: agents.length.toString().padStart(2, "0"),
      note: `${agents.filter((agent) => agent.directions.includes("outbound")).length} ready for browser calls`,
      icon: Bot,
      accent: "orange",
    },
  ];

  const openAgent = (name: string) => {
    setSelectedAgent(name);
    setAgentFilter(name);
    setPage("agents");
    setActionError("");
  };

  const endCallRecord = useCallback(async (callId: string): Promise<string | null> => {
    try {
      await apiRequest(`/calls/${encodeURIComponent(callId)}/end`, {
        method: "POST",
      });
      return null;
    } catch (error) {
      return error instanceof Error
        ? `Call ended, but its history could not be updated: ${error.message}`
        : "Call ended, but its history could not be updated.";
    }
  }, []);

  const stopCall = useCallback(async () => {
    const room = roomRef.current;
    const callId = callIdRef.current;
    if (!room && !callId) return;
    setCallState("ending");
    try {
      if (room) await room.localParticipant.setMicrophoneEnabled(false);
    } catch (error) {
      setActionError(
        error instanceof Error
          ? `Could not disable microphone before disconnecting: ${error.message}`
          : "Could not disable microphone before disconnecting.",
      );
    } finally {
      roomRef.current = null;
      callIdRef.current = null;
      room?.disconnect();
      const endRecordError = callId ? await endCallRecord(callId) : null;
      setCallState("idle");
      setAgentConnected(false);
      setCallStartedAt(null);
      setElapsed(0);
      setTranscripts([]);
      setPage("agents");
      setCallNotice("Call ended. Final transcript turns are saved locally.");
      if (endRecordError) setActionError(endRecordError);
      void refresh();
    }
  }, [endCallRecord, refresh]);

  const startCall = async (agent: Agent) => {
    setActionError("");
    setCallNotice("");
    setPlaybackError("");
    setTranscripts([]);
    setAgentConnected(false);
    setCallState("connecting");
    try {
      const credentials = await apiRequest<{
        call_id: string;
        token: string;
        url: string;
      }>("/calls", {
        method: "POST",
        body: JSON.stringify({
          agent_name: agent.name,
          direction: "outbound",
        }),
      });
      callIdRef.current = credentials.call_id;
      const room = new Room({ adaptiveStream: true, dynacast: true });
      roomRef.current = room;
      room.on(RoomEvent.ParticipantConnected, (participant) => {
        if (participant.isAgent) setAgentConnected(true);
      });
      room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        if (
          participant.isAgent &&
          !Array.from(room.remoteParticipants.values()).some((remote) => remote.isAgent)
        ) {
          setAgentConnected(false);
        }
      });
      room.on(
        RoomEvent.TrackSubscribed,
        (
          track: RemoteTrack,
          _publication: RemoteTrackPublication,
          _participant: RemoteParticipant,
        ) => {
          if (track.kind !== Track.Kind.Audio || !audioRef.current) return;
          const element = track.attach();
          element.autoplay = true;
          element.muted = false;
          element.volume = 1;
          audioRef.current.appendChild(element);
          void element.play().catch((error: unknown) => {
            setPlaybackError(
              error instanceof Error
                ? `Audio playback was blocked: ${error.message}`
                : "Audio playback was blocked by the browser.",
            );
          });
        },
      );
      room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        if (topic !== "myvoiceagent.transcript") return;
        try {
          const transcript = JSON.parse(new TextDecoder().decode(payload)) as {
            role?: string;
            text?: string;
            id?: string;
            final?: boolean;
            created_at?: number | null;
          };
          if (
            (transcript.role !== "user" && transcript.role !== "agent") ||
            typeof transcript.text !== "string" ||
            !transcript.text.trim()
          ) {
            return;
          }
          const nextLine: TranscriptLine = {
            id: transcript.id ?? crypto.randomUUID(),
            role: transcript.role,
            text: transcript.text,
            final: transcript.final !== false,
            created_at: transcript.created_at,
          };
          setTranscripts((previous) => {
            const existingIndex = previous.findIndex((line) => line.id === nextLine.id);
            if (existingIndex < 0) return [...previous, nextLine];
            const updated = [...previous];
            updated[existingIndex] = nextLine;
            return updated;
          });
        } catch (error) {
          setActionError(
            error instanceof Error
              ? `Could not read caller transcript: ${error.message}`
              : "Could not read caller transcript.",
          );
        }
      });
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => element.remove());
      });
      room.on(RoomEvent.Disconnected, () => {
        if (roomRef.current !== room) return;
        roomRef.current = null;
        const callId = callIdRef.current;
        callIdRef.current = null;
        setCallState("idle");
        setAgentConnected(false);
        setCallStartedAt(null);
        setElapsed(0);
        setTranscripts([]);
        setPage("agents");
        setCallNotice("The LiveKit connection ended.");
        if (callId) {
          void endCallRecord(callId).then((error) => {
            if (error) setActionError(error);
            return refresh();
          });
        }
      });
      await room.connect(credentials.url, credentials.token);
      setAgentConnected(
        Array.from(room.remoteParticipants.values()).some((participant) => participant.isAgent),
      );
      try {
        await room.startAudio();
      } catch (error) {
        setPlaybackError(
          error instanceof Error
            ? `Click Enable speaker to hear the agent: ${error.message}`
            : "Click Enable speaker to hear the agent.",
        );
      }
      await room.localParticipant.setMicrophoneEnabled(true);
      setCallStartedAt(Date.now());
      setCallState("live");
      setPage("call");
      setCallNotice(`Session ${credentials.call_id} · transcripts are being saved`);
    } catch (error) {
      roomRef.current?.disconnect();
      roomRef.current = null;
      const callId = callIdRef.current;
      callIdRef.current = null;
      const endRecordError = callId ? await endCallRecord(callId) : null;
      setCallState("idle");
      setAgentConnected(false);
      setCallStartedAt(null);
      setElapsed(0);
      setTranscripts([]);
      void refresh();
      const startError = error instanceof Error
        ? error.message
        : "Could not start the browser call.";
      setActionError(
        endRecordError
          ? `${startError} ${endRecordError}`
          : startError,
      );
    }
  };

  const enableAudio = async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.startAudio();
      const elements = audioRef.current?.querySelectorAll("audio") ?? [];
      await Promise.all(Array.from(elements, (element) => element.play()));
      setPlaybackError("");
    } catch (error) {
      setPlaybackError(
        error instanceof Error
          ? `Speaker playback is still blocked: ${error.message}`
          : "Speaker playback is still blocked. Check the browser's audio settings.",
      );
    }
  };

  const selectPage = (nextPage: Page) => {
    setPage(nextPage);
    setAgentFilter(null);
    setSelectedAgent(null);
    setActionError("");
  };

  const heading = page === "call"
    ? "Live call"
    : currentAgent
    ? currentAgent.display_name
    : {
        overview: "Overview",
        agents: "Your agents",
        transcripts: "Call transcripts",
        metrics: "Latency metrics",
      }[page];
  const subheading = page === "call"
    ? "Talk with your agent and follow both sides of the conversation."
    : currentAgent
    ? "Manage your voice agent and explore its performance."
    : {
        overview: "Monitor your voice workspace at a glance.",
        agents: "Build, configure, and talk to your agents.",
        transcripts: "Review saved caller and agent turns, call by call.",
        metrics: "Real turn-level latency from your LiveKit sessions.",
      }[page];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><AudioLines size={20} /></span>
          <span className="brand-name">myvoice<span>agent</span></span>
          <button className="icon-button collapse-button" aria-label="Collapse sidebar">
            <ChevronDown size={15} />
          </button>
        </div>
        <div className="workspace-switcher">
          <span className="workspace-avatar">M</span>
          <span className="workspace-name">My workspace<small>Developer plan</small></span>
          <ChevronDown size={15} className="muted-icon" />
        </div>
        <div className="side-label">WORKSPACE</div>
        <nav className="primary-nav">
          <NavButton icon={<LayoutDashboard size={17} />} label="Overview" active={page === "overview" && !currentAgent} onClick={() => selectPage("overview")} />
          <NavButton icon={<Bot size={17} />} label="Agents" active={(page === "agents" || page === "call") && !currentAgent} onClick={() => selectPage("agents")} count={agents.length} />
          <NavButton icon={<AudioLines size={17} />} label="Transcripts" active={page === "transcripts"} onClick={() => selectPage("transcripts")} />
          <NavButton icon={<Activity size={17} />} label="Latency metrics" active={page === "metrics"} onClick={() => selectPage("metrics")} />
        </nav>
        <div className="side-section-head">
          <span className="side-label">YOUR AGENTS</span>
        </div>
        <div className="agent-nav-list">
          {agents.map((agent, index) => (
            <button
              className={`agent-nav-item ${currentAgent?.name === agent.name ? "selected" : ""}`}
              key={agent.name}
              onClick={() => openAgent(agent.name)}
            >
              <span className={`agent-avatar avatar-${index % 4}`}>{initials(agent.display_name)}</span>
              <span className="agent-nav-copy">{agent.display_name}<small>{agent.directions.join(" · ")}</small></span>
              <span className="online-dot" />
            </button>
          ))}
          {!agents.length && !isRefreshing && <div className="sidebar-empty">No agents yet</div>}
        </div>
        <div className="sidebar-spacer" />
        <div className="sidebar-footer">
          <NavButton icon={<Settings2 size={17} />} label="Settings" onClick={() => setActionError("Settings are configured in each agent's config.json.")} />
          <NavButton icon={<CircleHelp size={17} />} label="Documentation" onClick={() => setActionError("See README.md for setup and local transcript storage.")} />
          <div className="profile-row">
            <div className="profile-avatar">V</div>
            <div className="profile-copy">Vamshi<small>Local workspace</small></div>
            <MoreHorizontal size={18} className="muted-icon" />
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><b>{heading}</b></div>
          <div className="topbar-actions">
            <span className={`system-status ${loadError ? "status-error" : ""}`}><span />{loadError ? "API disconnected" : "All systems operational"}</span>
            <button className="icon-button search-button" aria-label="Search" onClick={() => document.getElementById("agent-search")?.focus()}><Search size={17} /><kbd>⌘ K</kbd></button>
            <button className="top-avatar">V</button>
          </div>
        </header>
        <div className="page-content">
          {loadError && <div className="alert alert-error"><CircleHelp size={17} /><span>{loadError}</span><button className="icon-button" onClick={() => void refresh()}><Activity size={15} /></button></div>}
          {actionError && <div className="alert alert-error"><CircleHelp size={17} /><span>{actionError}</span><button className="alert-close" onClick={() => setActionError("")}><X size={15} /></button></div>}
          {callNotice && <div className="alert alert-info"><ShieldCheck size={17} /><span>{callNotice}</span><button className="alert-close" onClick={() => setCallNotice("")}><X size={15} /></button></div>}
          <div className="page-heading">
            <div><div className="eyebrow"><span className="eyebrow-dot" />VOICE WORKSPACE</div><h1>{heading}</h1><p>{subheading}</p></div>
            <div className="heading-actions">
              <button className="button button-secondary" onClick={() => void refresh()}><Activity size={15} className={isRefreshing ? "spin" : ""} /> Refresh</button>
            </div>
          </div>

          {page === "overview" && !currentAgent && (
            <Overview
              cards={overviewCards}
              agents={agents}
              calls={visibleCalls}
              metrics={metrics}
              onAgent={openAgent}
              onSeeCalls={() => selectPage("transcripts")}
            />
          )}
          {page === "agents" && (
            currentAgent ? (
              <AgentDetail
                agent={currentAgent}
                calls={visibleCalls}
                metrics={metrics}
                callState={callState}
                agentConnected={agentConnected}
                elapsed={elapsed}
                startCall={startCall}
                stopCall={stopCall}
              />
            ) : (
              <AgentList agents={agents} onAgent={openAgent} />
            )
          )}
          {page === "call" && currentAgent && (
            <CallPage
              agent={currentAgent}
              callState={callState}
              agentConnected={agentConnected}
              elapsed={elapsed}
              transcripts={transcripts}
              playbackError={playbackError}
              onEnableAudio={() => void enableAudio()}
              onEndCall={() => void stopCall()}
            />
          )}
          {page === "transcripts" && <TranscriptArchive calls={visibleCalls} />}
          {page === "metrics" && <MetricsPage metrics={metrics} calls={visibleCalls} />}
          <footer className="page-footer"><span>MYvoiceagent Studio <span className="footer-dot">·</span> Local development</span><span>Metrics are computed from actual LiveKit turn samples</span></footer>
        </div>
      </main>

      <div ref={audioRef} className="audio-mount" />
    </div>
  );
}

function NavButton({
  icon,
  label,
  active = false,
  count,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  count?: number;
  onClick: () => void;
}) {
  return <button className={`nav-button ${active ? "active" : ""}`} onClick={onClick}>{icon}<span>{label}</span>{count !== undefined && <small>{count}</small>}</button>;
}

function Overview({
  cards,
  agents,
  calls,
  metrics,
  onAgent,
  onSeeCalls,
}: {
  cards: { label: string; value: string; note: string; icon: typeof Phone; accent: string }[];
  agents: Agent[];
  calls: Call[];
  metrics: Metric[];
  onAgent: (name: string) => void;
  onSeeCalls: () => void;
}) {
  const metric = metrics.find((item) => item.key === "e2e_latency");
  return (
    <>
      <div className="stat-grid">
        {cards.map((card) => {
          const Icon = card.icon;
          return <div className="stat-card" key={card.label}><div className="stat-top"><span>{card.label}</span><span className={`stat-icon ${card.accent}`}><Icon size={17} /></span></div><strong>{card.value}</strong><small>{card.note}</small></div>;
        })}
      </div>
      <div className="overview-grid">
        <section className="panel performance-panel">
            <PanelHeader title="Response performance" subtitle="End of user speech to agent response" action="See calls" onAction={onSeeCalls} />
          {metric ? <LatencyChart metrics={metrics} /> : <EmptyChart />}
          <div className="chart-legend"><span><i className="legend-p50" /> P50 response</span><span><i className="legend-p90" /> P90 response</span><span className="legend-count">{metric ? `${metric.count} turn samples` : "No samples yet"}</span></div>
        </section>
        <section className="panel agent-panel">
          <PanelHeader title="Your agents" subtitle="Configured in your customer folder" action="View all" onAction={() => onAgent(agents[0]?.name ?? "")} />
          <div className="agent-card-list">
            {agents.slice(0, 4).map((agent, index) => <button className="agent-card-row" key={agent.name} onClick={() => onAgent(agent.name)}><span className={`agent-avatar avatar-${index % 4}`}>{initials(agent.display_name)}</span><span className="agent-row-name">{agent.display_name}<small>{agent.directions.join(" · ")}</small></span><span className="agent-live-tag"><i /> Ready</span><ChevronDown size={15} className="row-chevron" /></button>)}
            {!agents.length && <EmptyState title="No outbound agents yet" text="Add an agent folder with agent.py, config.json, and prompt/prompt.md under myagentrunner/customer/<AgentName>/outbound." />}
          </div>
        </section>
      </div>
      <section className="panel recent-panel">
        <PanelHeader title="Recent calls" subtitle="Latest sessions across your agents" action="All transcripts" onAction={onSeeCalls} />
        <CallTable calls={calls.slice(0, 5)} />
      </section>
    </>
  );
}

function AgentList({ agents, onAgent }: { agents: Agent[]; onAgent: (name: string) => void }) {
  if (!agents.length) return <div className="panel empty-agents"><EmptyState title="No outbound agents yet" text="Add an agent folder with agent.py, config.json, and prompt/prompt.md under myagentrunner/customer/<AgentName>/outbound." /></div>;
  return <div className="agent-grid">{agents.map((agent, index) => <button className="agent-tile panel" key={agent.name} onClick={() => onAgent(agent.name)}><div className="agent-tile-top"><span className={`agent-avatar large avatar-${index % 4}`}>{initials(agent.display_name)}</span><MoreHorizontal size={19} /></div><h3>{agent.display_name}</h3><p>Customer agent <span className="footer-dot">·</span> {agent.directions.join(", ")}</p><div className="model-list"><span><i /> STT <b>{agent.models.stt}</b></span><span><i /> LLM <b>{agent.models.llm}</b></span><span><i /> TTS <b>{agent.models.tts}</b></span></div><div className="agent-tile-bottom"><span><i className="online-dot" /> Configured</span><span>Open agent <ArrowUpRight size={14} /></span></div></button>)}</div>;
}

function AgentDetail({
  agent,
  calls,
  metrics,
  callState,
  agentConnected,
  elapsed,
  startCall,
  stopCall,
}: {
  agent: Agent;
  calls: Call[];
  metrics: Metric[];
  callState: "idle" | "connecting" | "live" | "ending";
  agentConnected: boolean;
  elapsed: number;
  startCall: (agent: Agent) => void;
  stopCall: () => void;
}) {
  const e2e = metrics.find((item) => item.key === "e2e_latency");
  return (
    <>
      <div className="agent-detail-banner panel">
        <div className="agent-banner-identity"><span className="agent-avatar large avatar-0">{initials(agent.display_name)}</span><div><div className="agent-name-line"><h2>{agent.display_name}</h2><span className="ready-pill"><i /> Ready</span></div><p>Customer agent <span className="footer-dot">·</span> {agent.directions.join(", ")}</p></div></div>
        <div className="agent-banner-actions">
          {callState === "live" || callState === "ending" ? <button className="button button-danger" onClick={() => void stopCall()} disabled={callState === "ending"}><Square size={14} fill="currentColor" /> {callState === "ending" ? "Ending…" : `End call · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}</button> : <button className="button button-primary" onClick={() => void startCall(agent)} disabled={callState === "connecting" || !agent.directions.includes("outbound")}><Phone size={15} /> {callState === "connecting" ? "Connecting…" : agent.directions.includes("outbound") ? "Start a call" : "No outbound agent"}</button>}
        </div>
      </div>
      {callState === "live" && <div className="call-live-panel"><span className="live-wave"><i /><i /><i /><i /><i /></span><div><b>{agentConnected ? "Agent connected" : "Waiting for agent"}</b><small>{agentConnected ? "Caller and agent turns are saved as a transcript." : "The room is open, but the LiveKit agent has not joined yet."}</small></div><span className="live-timer">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span><button className="button button-danger compact" onClick={() => void stopCall()}><Square size={13} fill="currentColor" /> End</button></div>}
      <div className="agent-info-grid">
        <section className="panel config-panel"><PanelHeader title="Voice configuration" subtitle="Loaded from this agent's config.json" /><div className="config-rows"><ConfigRow icon={<AudioLines size={16} />} title="Speech recognition" provider="Deepgram" model={agent.models.stt} /><ConfigRow icon={<Bot size={16} />} title="Language model" provider="Google Gemini" model={agent.models.llm} /><ConfigRow icon={<Mic size={16} />} title="Voice synthesis" provider="ElevenLabs" model={agent.models.tts} /></div><div className="config-footer"><span>Prompt & session settings</span><code>customer/{agent.name}/outbound/config.json</code></div></section>
        <section className="panel agent-performance"><PanelHeader title="Performance" subtitle="Measured from actual turns" /><div className="mini-metric"><div className="mini-metric-icon mint"><Activity size={17} /></div><span>End-to-end response</span><strong>{e2e ? `${e2e.p50_ms} <small>ms</small>` : "—"}</strong><small>{e2e ? `P90 ${e2e.p90_ms} ms · ${e2e.count} samples` : "No completed turns yet"}</small></div><div className="performance-footer"><span><Phone size={14} /> {calls.length} total calls</span><span><AudioLines size={14} /> {calls.filter((call) => call.transcript_available).length} transcripts</span></div></section>
      </div>
      <section className="panel recent-panel"><PanelHeader title="Recent calls" subtitle={`Most recent sessions with ${agent.display_name}`} /><CallTable calls={calls.slice(0, 6)} /></section>
    </>
  );
}

function CallPage({
  agent,
  callState,
  agentConnected,
  elapsed,
  transcripts,
  playbackError,
  onEnableAudio,
  onEndCall,
}: {
  agent: Agent;
  callState: "idle" | "connecting" | "live" | "ending";
  agentConnected: boolean;
  elapsed: number;
  transcripts: TranscriptLine[];
  playbackError: string;
  onEnableAudio: () => void;
  onEndCall: () => void;
}) {
  const transcriptEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [transcripts]);

  return (
    <section className="call-page">
      <div className="call-page-toolbar panel">
        <div className="agent-banner-identity">
          <span className="agent-avatar large avatar-0">{initials(agent.display_name)}</span>
          <div>
            <div className="agent-name-line"><h2>{agent.display_name}</h2><span className={`ready-pill ${callState === "live" && agentConnected ? "" : "call-not-ready"}`}><i /> {callState === "live" ? agentConnected ? "Agent connected" : "Waiting for agent" : callState === "connecting" ? "Connecting" : "Disconnected"}</span></div>
            <p>{callState === "live" ? agentConnected ? "Microphone is on · transcript is being saved" : "Room connected · waiting for the agent worker to join" : "LiveKit voice session"}</p>
          </div>
        </div>
        <div className="call-page-controls">
          <span className="live-timer">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span>
          {playbackError && <button className="button button-secondary" onClick={onEnableAudio}><Headphones size={14} /> Enable speaker</button>}
          {callState === "live" && <button className="button button-danger" onClick={onEndCall}><Square size={13} fill="currentColor" /> End call</button>}
        </div>
      </div>
      {playbackError && <div className="alert alert-error"><Headphones size={16} /><span>{playbackError}</span><button className="button button-secondary compact" onClick={onEnableAudio}>Enable speaker</button></div>}
      <div className="transcript-panel panel">
        <div className="transcript-header"><div><h2>Conversation</h2><p>Caller and agent transcripts appear here as the call progresses.</p></div><span className="transcript-count">{transcripts.length} {transcripts.length === 1 ? "message" : "messages"}</span></div>
        <div className="transcript-list" aria-live="polite" aria-relevant="additions text">
          {!transcripts.length && <div className="transcript-empty"><AudioLines size={22} /><b>Waiting for the conversation</b><span>Caller speech and agent replies will appear here turn by turn.</span></div>}
          {transcripts.map((line) => <article className={`transcript-line transcript-${line.role}`} key={line.id}><span className="transcript-speaker">{line.role === "user" ? "You" : agent.display_name}</span><p>{line.text}{!line.final && <span className="transcript-interim"> · listening</span>}</p></article>)}
          <div ref={transcriptEndRef} />
        </div>
      </div>
    </section>
  );
}

function ConfigRow({ icon, title, provider, model }: { icon: React.ReactNode; title: string; provider: string; model: string }) {
  return <div className="config-row"><span className="config-icon">{icon}</span><span className="config-title">{title}<small>{provider}</small></span><code>{model}</code><span className="config-active"><i /> Active</span></div>;
}

function TranscriptArchive({ calls }: { calls: Call[] }) {
  const transcriptCalls = calls.filter((call) => call.transcript_available);
  const [selectedCallId, setSelectedCallId] = useState<string | null>(
    transcriptCalls[0]?.id ?? null,
  );
  const [transcripts, setTranscripts] = useState<TranscriptLine[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!transcriptCalls.some((call) => call.id === selectedCallId)) {
      setSelectedCallId(transcriptCalls[0]?.id ?? null);
    }
  }, [calls, selectedCallId]);

  useEffect(() => {
    if (!selectedCallId) {
      setTranscripts([]);
      return;
    }
    let active = true;
    setLoading(true);
    setError("");
    apiRequest<TranscriptLine[]>(`/transcripts/${encodeURIComponent(selectedCallId)}`)
      .then((result) => {
        if (active) setTranscripts(result);
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "Could not load this transcript.",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedCallId]);

  const selectedCall = transcriptCalls.find((call) => call.id === selectedCallId);
  return (
    <div className="transcript-archive">
      <section className="panel transcript-archive-calls">
        <PanelHeader title="Saved conversations" subtitle="Transcript files for completed and active calls" />
        <div className="transcript-call-list">
          {transcriptCalls.map((call) => (
            <button
              className={`transcript-call-row ${call.id === selectedCallId ? "selected" : ""}`}
              key={call.id}
              onClick={() => setSelectedCallId(call.id)}
            >
              <span className="transcript-icon"><AudioLines size={17} /></span>
              <span><b>{call.agent_name}</b><small>{formatDate(call.started_at)} · {formatDuration(call)}</small></span>
            </button>
          ))}
          {!transcriptCalls.length && <EmptyState title="No transcripts yet" text="Each call will be saved here as caller and agent turns." />}
        </div>
      </section>
      <section className="panel transcript-archive-detail">
        <PanelHeader
          title={selectedCall ? `${selectedCall.agent_name} conversation` : "Conversation"}
          subtitle={selectedCall ? `${formatDate(selectedCall.started_at)} · ${selectedCall.direction}` : "Select a saved call to review its turns"}
        />
        {error && <div className="alert alert-error">{error}</div>}
        {loading ? <div className="transcript-archive-message">Loading transcript…</div> : selectedCall ? (
          <div className="transcript-list" aria-live="polite">
            {transcripts.map((line) => (
              <article className={`transcript-line transcript-${line.role}`} key={line.id}>
                <span className="transcript-speaker">{line.role === "user" ? "Caller" : selectedCall.agent_name}</span>
                <p>{line.text}</p>
              </article>
            ))}
            {!transcripts.length && !error && <div className="transcript-archive-message">No final transcript turns have been saved for this call yet.</div>}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function MetricsPage({ metrics, calls }: { metrics: Metric[]; calls: Call[] }) {
  return <><div className="metric-intro panel"><span className="metric-intro-icon"><Activity size={19} /></span><div><b>Measured voice latency</b><p>P50 and P90 are calculated from actual LiveKit per-turn samples. Each metric is shown separately—these are not combined into a synthetic total.</p></div><span className="sample-count">{calls.length} calls</span></div><section className="panel latency-panel"><PanelHeader title="Latency distribution" subtitle="Milliseconds · lower is faster" />{metrics.length ? <LatencyChart metrics={metrics} /> : <EmptyState title="Waiting for your first turn" text="Latency samples are saved when the agent finishes talking to a caller." />}<div className="chart-legend"><span><i className="legend-p50" /> P50</span><span><i className="legend-p90" /> P90</span><span className="legend-count">Actual samples only</span></div></section><div className="metric-card-grid">{metrics.map((metric) => <div className="panel metric-card" key={metric.key}><span>{metric.label}</span><div><strong>{metric.p50_ms}</strong><small>ms P50</small></div><div><b>{metric.p90_ms}</b><small>ms P90</small></div><footer>{metric.count} samples</footer></div>)}</div></>;
}

function LatencyChart({ metrics }: { metrics: Metric[] }) {
  const data = metrics.map((metric) => ({ name: metric.key.split("_").join(" "), P50: metric.p50_ms, P90: metric.p90_ms }));
  return <div className="latency-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={data} layout="vertical" margin={{ top: 3, right: 20, bottom: 0, left: 4 }} barGap={4}><CartesianGrid stroke="#242a31" horizontal={false} /><XAxis type="number" tick={{ fill: "#737e89", fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis type="category" dataKey="name" tick={{ fill: "#a6afb8", fontSize: 11 }} width={142} axisLine={false} tickLine={false} /><Tooltip cursor={{ fill: "#181d23" }} contentStyle={{ background: "#12161b", border: "1px solid #2a3037", borderRadius: 8, color: "#e6eaee" }} formatter={(value) => [`${value} ms`]} /><Bar dataKey="P50" fill="#83e1c0" radius={[0, 4, 4, 0]} barSize={8} /><Bar dataKey="P90" fill="#546c66" radius={[0, 4, 4, 0]} barSize={8} /></BarChart></ResponsiveContainer></div>;
}

function EmptyChart() {
  return <div className="empty-chart"><div className="empty-chart-grid"><span /><span /><span /><span /></div><div className="empty-chart-message"><Activity size={20} /><span>Latency appears after your first completed call</span></div></div>;
}

function CallTable({ calls }: { calls: Call[] }) {
  return <div className="table-wrap"><table><thead><tr><th>AGENT</th><th>DATE</th><th>DIRECTION</th><th>DURATION</th><th>TRANSCRIPT</th><th /></tr></thead><tbody>{calls.map((call, index) => <tr key={call.id}><td><span className={`table-agent-avatar avatar-${index % 4}`}>{initials(call.agent_name)}</span><b>{call.agent_name}</b></td><td>{formatDate(call.started_at)}</td><td><span className="direction-tag"><ArrowUpRight size={13} /> {call.direction}</span></td><td>{formatDuration(call)}</td><td><span className={`transcript-pill ${call.transcript_available ? "available" : ""}`}><i />{call.transcript_available ? "saved" : "pending"}</span></td><td><button className="icon-button small-icon" aria-label="Call options"><MoreHorizontal size={17} /></button></td></tr>)}</tbody></table>{!calls.length && <EmptyState title="No calls yet" text="Start a browser session from an agent to see call activity here." />}</div>;
}

function PanelHeader({ title, subtitle, action, onAction }: { title: string; subtitle?: string; action?: string; onAction?: () => void }) {
  return <div className="panel-header"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{action && <button className="text-action" onClick={onAction}>{action}<ArrowUpRight size={14} /></button>}</div>;
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="empty-state"><span className="empty-state-icon"><Radio size={19} /></span><b>{title}</b><p>{text}</p></div>;
}

export default App;
