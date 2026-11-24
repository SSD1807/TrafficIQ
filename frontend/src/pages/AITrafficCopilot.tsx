
import { useState, useRef, useEffect } from 'react';
import {
  Bot,
  Send,
  Sparkles,
  User,
  ChevronDown,
  Clock,
  Activity,
} from 'lucide-react';

import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import {
  ProvenanceBanner,
  SourceTag,
} from '@/components/ui/Provenance';

import { api, ApiError } from '@/lib/api';
import { describeProviderState } from '@/lib/incidentStatus';
import { useApi } from '@/lib/useApi';

import type {
  IncidentProviderState,
  ChatMessage,
  CopilotResponse,
} from '@/types';

const SUGGESTIONS = [
  'Are there any current traffic incidents in Bhubaneswar?',
  'Which roads are currently closed?',
  'What is the current weather?',
  'Which locations have predicted traffic congestion?',
  'Give me the 30 minute forecast for IT Park',
  'Are there any active incidents?',
  'Check TomTom live traffic near Rasulgarh',
  'Check TomTom traffic incidents in Bhubaneswar',
  'Show the best route from Rasulgarh to Patia',
];

function formatIST(value: string | number | undefined) {
  if (value === undefined || value === null) return 'Time unavailable';

  const date =
    typeof value === 'number'
      ? new Date(value * 1000)
      : new Date(value);

  if (Number.isNaN(date.getTime())) return 'Time unavailable';

  return date.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}

function ModeTag({ mode }: { mode?: string }) {
  switch (mode) {
    case 'ollama_tool_calling':
      return (
        <SourceTag
          kind="llm"
          label="Ollama · tool-grounded"
        />
      );

    case 'openai_function_calling':
      return (
        <SourceTag
          kind="llm"
          label="OpenAI · tool-grounded"
        />
      );

    case 'rule_based_after_ollama_error':
      return (
        <SourceTag
          kind="rule-based"
          label="Rule-based · Ollama fallback"
        />
      );

    case 'rule_based_after_openai_error':
    case 'rule_based_after_llm_error':
      return (
        <SourceTag
          kind="rule-based"
          label="Rule-based · LLM fallback"
        />
      );

    case 'rule_based':
      return <SourceTag kind="rule-based" />;

    default:
      return (
        <SourceTag
          kind="rule-based"
          label={mode || 'Backend response'}
        />
      );
  }
}

/** Render a safe, lightweight subset of Markdown. */
function InlineMarkdown({ text }: { text: string }) {
  const parts = text.split(/(\*\*.*?\*\*)/g);

  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong key={index} className="font-semibold text-white">
              {part.slice(2, -2)}
            </strong>
          );
        }

        return <span key={index}>{part}</span>;
      })}
    </>
  );
}

function MessageContent({ content }: { content: string }) {
  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];

  let bulletItems: string[] = [];

  const flushBullets = () => {
    if (bulletItems.length === 0) return;

    elements.push(
      <ul
        key={`list-${elements.length}`}
        className="my-2 list-disc space-y-1 pl-5"
      >
        {bulletItems.map((item, index) => (
          <li key={index}>
            <InlineMarkdown text={item} />
          </li>
        ))}
      </ul>
    );

    bulletItems = [];
  };

  lines.forEach((line, index) => {
    const trimmed = line.trim();

    const bulletMatch = trimmed.match(/^[-*]\s+(.+)$/);

    if (bulletMatch) {
      bulletItems.push(bulletMatch[1]);
      return;
    }

    flushBullets();

    if (!trimmed) return;

    elements.push(
      <p key={`p-${index}`} className="my-1 leading-relaxed">
        <InlineMarkdown text={trimmed} />
      </p>
    );
  });

  flushBullets();

  return <div>{elements}</div>;
}

interface IncidentToolResult {
  provider?: { state?: IncidentProviderState; message?: string };
  incidents?: { source?: string }[];
  incidents_on_original_route?: { source?: string }[];
}

function ToolSummary({
  toolResults,
}: {
  toolResults: Record<string, unknown>;
}) {
  const flowResults = toolResults.tomtom_flow as
    | Record<string, Record<string, unknown>>
    | undefined;

  const incidentResult = toolResults.tomtom_incidents as
    | Record<string, unknown>
    | undefined;

  const directFlow = toolResults.get_tomtom_flow as
    | Record<string, unknown>
    | undefined;

  const flow = directFlow ||
    (flowResults
      ? Object.values(flowResults)[0]
      : undefined);

  const incidents = Array.isArray(incidentResult?.incidents)
    ? incidentResult.incidents.length
    : undefined;

  if (flow) {
    const fetchedAt = flow.fetched_at as number | undefined;
    const cache = flow.cache_status as string | undefined;

    return (
      <div className="mt-3 space-y-2 rounded-lg border border-navy-700/70 bg-navy-950/50 p-3 text-xs">
        <div className="flex items-center gap-2 text-slate-300">
          <Activity className="h-4 w-4 text-cyan-400" />
          <strong>Traffic measurement</strong>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-slate-500">Current speed</p>
            <p className="mt-1 text-base font-semibold text-white">
              {String(flow.current_speed_kph ?? '—')} km/h
            </p>
          </div>

          <div>
            <p className="text-slate-500">Free-flow speed</p>
            <p className="mt-1 text-base font-semibold text-white">
              {String(flow.free_flow_speed_kph ?? '—')} km/h
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge variant="success">
            {cache === 'network_refresh'
              ? 'Fresh network response'
              : cache === 'fresh_cache'
                ? 'Cached provider response'
                : cache || 'Freshness unknown'}
          </Badge>

          {flow.road_closure === true && (
            <Badge variant="danger">Road closure reported</Badge>
          )}

          {flow.road_closure === false && (
            <Badge variant="neutral">No closure reported</Badge>
          )}
        </div>

        <p className="text-slate-400">
          Source: {String(flow.source || 'Unknown')}
        </p>

        {fetchedAt !== undefined && (
          <p className="flex items-center gap-1 text-slate-500">
            <Clock className="h-3 w-3" />
            Fetched: {formatIST(fetchedAt)}
          </p>
        )}
      </div>
    );
  }

  const feedResult = (toolResults.incident_feed ?? toolResults.route_incident_impact) as IncidentToolResult | undefined;
  if (feedResult) {
    const provider = feedResult.provider;
    const state = provider?.state ? describeProviderState(provider.state) : null;
    const list = feedResult.incidents ?? feedResult.incidents_on_original_route ?? [];
    const tomtom = list.filter((i) => i.source === 'tomtom').length;
    return (
      <div className="mt-3 space-y-1 rounded-lg border border-navy-700/70 bg-navy-950/50 p-3 text-xs" data-testid="incident-tool-summary">
        <p className="font-semibold text-slate-200">Incident data used for this answer</p>
        <div className="flex flex-wrap gap-2">
          {state && <Badge variant={state.tone === 'success' ? 'success' : state.tone === 'danger' ? 'danger' : state.tone === 'warning' ? 'warning' : 'neutral'}>TomTom: {state.label}</Badge>}
          <Badge variant="info">{tomtom} TomTom live</Badge>
          <Badge variant="neutral">{list.length - tomtom} manual/simulated (test data)</Badge>
          {toolResults.route_incident_impact !== undefined && <Badge variant="info">route compared with / without incidents</Badge>}
        </div>
        {provider?.message && <p className="text-slate-400">{provider.message}</p>}
      </div>
    );
  }

  if (incidentResult) {
    return (
      <div className="mt-3 rounded-lg border border-navy-700/70 bg-navy-950/50 p-3 text-xs">
        <p className="font-semibold text-slate-200">
          TomTom incident retrieval
        </p>

        <p className="mt-2 text-slate-400">
          Records returned: {incidents ?? 'Unknown'}
        </p>

        <p className="mt-1 text-slate-400">
          Source: {String(incidentResult.source || 'Unknown')}
        </p>

        <p className="mt-1 text-slate-400">
          Cache: {String(incidentResult.cache_status || 'Unknown')}
        </p>
      </div>
    );
  }

  return null;
}

export function AITrafficCopilot() {
  const health = useApi(() => api.getHealth(), []);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'intro',
      role: 'assistant',
      timestamp: new Date().toISOString(),
      content:
        'I am the Traffic Copilot. I can use local Ollama and backend tools to answer questions about weather, live TomTom traffic, predictions, forecasts, incidents and routes. I distinguish live provider data from historical synthetic data.',
    },
  ]);

  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({
      behavior: 'smooth',
    });
  }, [messages, busy]);

  const send = async (text: string) => {
    const value = text.trim();

    if (!value || busy) return;

    setMessages((previous) => [
      ...previous,
      {
        id: `u${Date.now()}`,
        role: 'user',
        content: value,
        timestamp: new Date().toISOString(),
      },
    ]);

    setInput('');
    setBusy(true);

    try {
      const response: CopilotResponse =
        await api.copilotChat(value);

      setMessages((previous) => [
        ...previous,
        {
          id: `a${Date.now()}`,
          role: 'assistant',
          content: response.answer,
          timestamp: new Date().toISOString(),
          mode: response.mode,
          toolResults: response.tool_results,
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        {
          id: `e${Date.now()}`,
          role: 'assistant',
          isError: true,
          timestamp: new Date().toISOString(),
          content:
            error instanceof ApiError
              ? error.message
              : 'The Copilot request failed.',
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Traffic Copilot"
        description="AI assistant powered by local Ollama and tool-grounded backend services"
        icon={<Bot className="h-6 w-6" />}
        action={
          <Badge variant={health.data ? 'success' : 'neutral'}>
            <Sparkles className="h-3 w-3" />
            {health.loading
              ? 'Checking backend…'
              : health.data
                ? 'Backend connected'
                : 'Backend status unavailable'}
          </Badge>
        }
      />

      <ProvenanceBanner>
        The Copilot uses local Ollama when configured on the backend.
        Individual responses identify whether they came from Ollama
        tool calling or the deterministic rule-based fallback.
        Live traffic measurements are supplied by TomTom; historical
        ML predictions use synthetic traffic data.
      </ProvenanceBanner>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
        <Card
          className="lg:col-span-3"
          title="Chat"
          subtitle="POST /copilot/chat"
          icon={<Bot className="h-5 w-5" />}
        >
          <div className="flex h-[480px] flex-col">
            <div
              className="flex-1 space-y-4 overflow-y-auto pr-2"
              aria-live="polite"
            >
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`flex gap-3 ${
                    message.role === 'user'
                      ? 'flex-row-reverse'
                      : ''
                  }`}
                >
                  <div
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                      message.role === 'user'
                        ? 'bg-navy-700 text-slate-300'
                        : 'bg-gradient-to-br from-cyan-400 to-teal-500 text-navy-950'
                    }`}
                  >
                    {message.role === 'user' ? (
                      <User className="h-4 w-4" />
                    ) : (
                      <Bot className="h-4 w-4" />
                    )}
                  </div>

                  <div
                    className={`max-w-[85%] min-w-0 rounded-xl px-4 py-3 text-sm ${
                      message.role === 'user'
                        ? 'bg-navy-700/50 text-slate-200'
                        : message.isError
                          ? 'border border-rose-500/30 bg-rose-500/10 text-rose-200'
                          : 'border border-cyan-500/20 bg-cyan-500/5 text-slate-200'
                    }`}
                  >
                    <MessageContent content={message.content} />

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {message.role === 'assistant' &&
                        message.mode && (
                          <ModeTag mode={message.mode} />
                        )}

                      <span className="text-[10px] text-slate-500">
                        {formatIST(message.timestamp)}
                      </span>
                    </div>

                    {message.toolResults &&
                      Object.keys(message.toolResults).length > 0 && (
                        <>
                          <ToolSummary
                            toolResults={message.toolResults}
                          />

                          <details className="mt-3 text-xs text-slate-400">
                            <summary className="flex cursor-pointer items-center gap-1">
                              <ChevronDown className="h-3 w-3" />
                              Tool results (
                              {Object.keys(message.toolResults).join(', ')})
                            </summary>

                            <pre className="mt-2 max-h-56 overflow-auto rounded bg-navy-950/60 p-2 text-[10px] leading-snug">
                              {JSON.stringify(
                                message.toolResults,
                                null,
                                2
                              )}
                            </pre>
                          </details>
                        </>
                      )}
                  </div>
                </div>
              ))}

              {busy && (
                <div
                  className="flex gap-3"
                  role="status"
                  aria-label="Copilot is working"
                >
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-400 to-teal-500 text-navy-950">
                    <Bot className="h-4 w-4" />
                  </div>

                  <div className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-4 py-3">
                    <div className="flex gap-1">
                      {[0, 150, 300].map((delay) => (
                        <span
                          key={delay}
                          className="h-2 w-2 animate-bounce rounded-full bg-cyan-400"
                          style={{
                            animationDelay: `${delay}ms`,
                          }}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              )}

              <div ref={endRef} />
            </div>

            <div className="mt-4 flex gap-2 border-t border-navy-700/60 pt-4">
              <input
                type="text"
                value={input}
                onChange={(event) =>
                  setInput(event.target.value)
                }
                onKeyDown={(event) =>
                  event.key === 'Enter' && send(input)
                }
                aria-label="Message the copilot"
                placeholder="Ask about live traffic, weather, predictions, forecasts or routes…"
                maxLength={4000}
                className="flex-1 rounded-lg border border-navy-600/50 bg-navy-800/60 px-4 py-2.5 text-sm text-slate-200 placeholder:text-slate-500 outline-none focus:border-cyan-500/50"
              />

              <button
                onClick={() => send(input)}
                disabled={!input.trim() || busy}
                aria-label="Send message"
                className="flex h-10 w-10 items-center justify-center rounded-lg bg-cyan-500 text-navy-950 transition-colors hover:bg-cyan-400 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card
            title="Try asking"
            subtitle="Suggested Copilot questions"
            icon={<Sparkles className="h-5 w-5" />}
          >
            <div className="space-y-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  disabled={busy}
                  className="w-full rounded-lg border border-navy-700/50 bg-navy-800/40 p-3 text-left text-xs text-slate-300 transition-colors hover:border-cyan-500/30 hover:text-slate-100 disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </Card>

          <Card
            title="Backend tools"
            subtitle="Data sources used by the Copilot"
          >
            <ul className="space-y-2 text-xs text-slate-400">
              {[
                'Ollama local LLM',
                'TomTom live traffic flow',
                'TomTom traffic incidents',
                'Open-Meteo weather',
                'ML congestion predictions',
                '30/60-minute forecasts',
                'Incident management',
                'Dijkstra and A* routing',
              ].map((item) => (
                <li
                  key={item}
                  className="flex items-center gap-2"
                >
                  <Bot className="h-3.5 w-3.5 text-cyan-400" />
                  {item}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
