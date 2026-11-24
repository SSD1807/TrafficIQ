
import { useState } from 'react';
import {
  Gauge,
  Target,
  BarChart3,
  Cpu,
  Award,
  Grid3x3,
  Route,
  AlertTriangle,
  CloudRain,
} from 'lucide-react';

import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Select } from '@/components/ui/Select';
import {
  LoadingState,
  ErrorState,
  EmptyState,
  UnavailableNote,
} from '@/components/ui/States';
import {
  ProvenanceBanner,
  SourceTag,
} from '@/components/ui/Provenance';
import { HorizontalBars } from '@/components/charts';
import { api } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { pct } from '@/lib/utils';

const CLASS_METRICS = [
  { value: 'accuracy', label: 'Accuracy' },
  { value: 'f1_macro', label: 'Macro F1' },
  { value: 'precision_macro', label: 'Macro precision' },
  { value: 'recall_macro', label: 'Macro recall' },
] as const;

type ClassMetricKey = (typeof CLASS_METRICS)[number]['value'];

const CLASSES = ['Low', 'Medium', 'High'];

const EDGE_COST_COMPONENTS = [
  {
    component: 'Congestion class',
    effect:
      'Multiplier 1.00 / 1.25 / 1.75 for Low / Medium / High, from simulated edge conditions when requested.',
  },
  {
    component: 'Measured provider flow',
    effect:
      'Replaces the congestion multiplier on matched edges by free-flow speed / current speed, bounded to 1–6.',
  },
  {
    component: 'Rainfall R (mm)',
    effect:
      'Multiplier 1 + 0.012 × min(R, 30), up to 1.36, applied when a rainfall value is supplied.',
  },
  {
    component: 'Accident, severity s',
    effect:
      'Multiplier 1 + 2s and additive delay 30 + 180s seconds.',
  },
  {
    component: 'Road work / obstruction / hazard',
    effect:
      'Multiplier 1 + 0.8s and additive delay 15 + 90s seconds.',
  },
  {
    component: 'Road closure',
    effect:
      'The directed edge is removed from the routing graph.',
  },
];

export function ModelEvaluation() {
  const ev = useApi(() => api.getModelEvaluation(), []);

  const [metric, setMetric] =
    useState<ClassMetricKey>('f1_macro');

  const [model, setModel] = useState('Random Forest');

  const [horizon, setHorizon] =
    useState<'30m' | '60m'>('30m');

  const [target, setTarget] =
    useState<'vehicle_count' | 'avg_speed'>('avg_speed');

  const [regMetric, setRegMetric] =
    useState<'mae' | 'rmse' | 'smape_pct'>('mae');

  const d = ev.data;

  const holdout = d?.classification?.holdout_test_metrics ?? {};

  const modelNames = Object.keys(holdout);

  const selected = holdout[model] ?? holdout[modelNames[0]];

  const meta = d?.classifier_metadata;

  const fc = d?.forecasting?.results?.[horizon];

  const regRows = fc ? Object.entries(fc.targets[target]) : [];

  return (
    <div className="space-y-6">

      {/* PAGE HEADER */}

      <PageHeader
        title="Model Evaluation"
        description="Holdout metrics read from the saved training artifacts (nothing is recomputed in the browser)"
        icon={<Gauge className="h-6 w-6" />}
        action={
          modelNames.length > 0 ? (
            <div className="flex flex-wrap items-end gap-3">
              <Select
                label="Classifier"
                id="ev-model"
                value={model}
                onChange={setModel}
                options={modelNames.map((m) => ({
                  value: m,
                  label: m,
                }))}
              />

              <Select
                label="Metric"
                id="ev-metric"
                value={metric}
                onChange={(v) => setMetric(v as ClassMetricKey)}
                options={[...CLASS_METRICS]}
              />
            </div>
          ) : undefined
        }
      />

      {/* DATA PROVENANCE */}

      <ProvenanceBanner tone="warn">
        <strong>Synthetic-data metrics.</strong>{' '}
        {d?.notice ??
          'Metrics describe a chronological holdout of the bundled synthetic dataset, not real road traffic.'}
      </ProvenanceBanner>

      {ev.loading ? (
        <Card>
          <LoadingState label="Loading saved evaluation artifacts…" />
        </Card>
      ) : ev.error ? (
        <Card>
          <ErrorState message={ev.error} onRetry={ev.reload} />
        </Card>
      ) : !d || !selected ? (
        <Card>
          <EmptyState message="No evaluation artifacts were returned." />
        </Card>
      ) : (
        <>

          {/* CLASSIFICATION SUMMARY */}

          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Selected classifier"
              value={meta?.selected_model ?? '—'}
              change="by validation macro-F1"
              icon={<Award className="h-5 w-5" />}
            />

            <StatCard
              label={`${model} accuracy`}
              value={pct(selected.accuracy)}
              icon={<Target className="h-5 w-5" />}
            />

            <StatCard
              label={`${model} macro F1`}
              value={pct(selected.f1_macro)}
              icon={<Target className="h-5 w-5" />}
            />

            <StatCard
              label="High-class recall"
              value={pct(
                selected.classification_report?.High?.recall
              )}
              change={`n=${selected.classification_report?.High?.support ?? '—'}`}
              icon={<Target className="h-5 w-5" />}
            />
          </div>

          {/* CLASSIFIER COMPARISON AND CONFUSION MATRIX */}

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

            <Card
              title="Classifier comparison"
              subtitle={`${CLASS_METRICS.find((m) => m.value === metric)?.label} on the holdout test split (%)`}
              icon={<BarChart3 className="h-5 w-5" />}
              action={<SourceTag kind="artifact" />}
            >
              <HorizontalBars
                data={modelNames.map((n) => ({
                  name: n,
                  value: +(holdout[n][metric] * 100).toFixed(2),
                }))}
                valueLabel={
                  CLASS_METRICS.find((m) => m.value === metric)!.label +
                  ' %'
                }
                height={240}
                domain={[0, 100]}
                leftMargin={110}
                colorFor={(n) =>
                  n === meta?.selected_model ? '#22d3ee' : '#2dd4bf'
                }
              />

              <p className="mt-2 text-xs text-slate-500">
                The “Persistence baseline” repeats the current label;
                it is a comparator, not a learned model.
              </p>
            </Card>

            <Card
              title={`Confusion matrix — ${model}`}
              subtitle="Rows = true class, columns = predicted class (holdout)"
              icon={<Grid3x3 className="h-5 w-5" />}
            >
              <div className="overflow-x-auto">
                <table className="w-full text-center text-sm">
                  <thead>
                    <tr className="text-xs text-slate-400">
                      <th className="pb-2 text-left">
                        true ↓ / pred →
                      </th>

                      {CLASSES.map((c) => (
                        <th key={c} className="pb-2">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>

                  <tbody>
                    {selected.confusion_matrix.map((row, i) => (
                      <tr
                        key={i}
                        className="border-t border-navy-800/50"
                      >
                        <td className="py-2 text-left text-slate-300">
                          {CLASSES[i]}
                        </td>

                        {row.map((v, j) => (
                          <td
                            key={j}
                            className={`py-2 font-semibold ${
                              i === j
                                ? 'text-teal-300'
                                : v > 0
                                  ? 'text-amber-300'
                                  : 'text-slate-500'
                            }`}
                          >
                            {v}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <table className="mt-4 w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-400">
                    <th className="pb-1">Class</th>
                    <th>Precision</th>
                    <th>Recall</th>
                    <th>F1</th>
                    <th>Support</th>
                  </tr>
                </thead>

                <tbody>
                  {CLASSES.map((c) => {
                    const r = selected.classification_report[c];

                    return r ? (
                      <tr
                        key={c}
                        className="border-t border-navy-800/50 text-slate-300"
                      >
                        <td className="py-1.5">{c}</td>
                        <td>{pct(r.precision)}</td>
                        <td>{pct(r.recall)}</td>
                        <td>{pct(r['f1-score'])}</td>
                        <td>{r.support}</td>
                      </tr>
                    ) : null;
                  })}
                </tbody>
              </table>
            </Card>

          </div>

          {/* FORECASTING EVALUATION */}

          <Card
            title="30 / 60-minute forecast regressors"
            subtitle="Holdout error per model (lower is better)"
            icon={<Cpu className="h-5 w-5" />}
            action={
              <div className="flex flex-wrap gap-2">
                <Select
                  value={horizon}
                  onChange={(v) => setHorizon(v as '30m' | '60m')}
                  options={[
                    { value: '30m', label: '+30 min' },
                    { value: '60m', label: '+60 min' },
                  ]}
                />

                <Select
                  value={target}
                  onChange={(v) =>
                    setTarget(v as typeof target)
                  }
                  options={[
                    {
                      value: 'avg_speed',
                      label: 'Avg speed (km/h)',
                    },
                    {
                      value: 'vehicle_count',
                      label: 'Vehicle count',
                    },
                  ]}
                />

                <Select
                  value={regMetric}
                  onChange={(v) =>
                    setRegMetric(v as typeof regMetric)
                  }
                  options={[
                    { value: 'mae', label: 'MAE' },
                    { value: 'rmse', label: 'RMSE' },
                    { value: 'smape_pct', label: 'sMAPE %' },
                  ]}
                />
              </div>
            }
          >
            {!fc ? (
              <EmptyState message="No forecast metrics available." />
            ) : (
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

                <HorizontalBars
                  data={regRows.map(([n, m]) => ({
                    name: n,
                    value: +m[regMetric].toFixed(3),
                  }))}
                  valueLabel={regMetric.toUpperCase()}
                  height={240}
                  leftMargin={110}
                  colorFor={(n) =>
                    n === 'Persistence' || n === 'Moving Average'
                      ? '#64748b'
                      : '#22d3ee'
                  }
                />

                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-navy-700 text-left text-xs text-slate-400">
                        <th className="pb-3 pr-4 font-medium">
                          Model
                        </th>
                        <th className="pb-3 pr-4 font-medium">
                          MAE
                        </th>
                        <th className="pb-3 pr-4 font-medium">
                          RMSE
                        </th>
                        <th className="pb-3 pr-4 font-medium">
                          sMAPE %
                        </th>
                        <th className="pb-3 font-medium">n</th>
                      </tr>
                    </thead>

                    <tbody>
                      {regRows.map(([n, m]) => (
                        <tr
                          key={n}
                          className="border-b border-navy-800/50 text-slate-300"
                        >
                          <td className="py-2.5 pr-4 font-medium text-slate-200">
                            {n}
                          </td>
                          <td className="pr-4">{m.mae.toFixed(2)}</td>
                          <td className="pr-4">{m.rmse.toFixed(2)}</td>
                          <td className="pr-4">
                            {m.smape_pct.toFixed(2)}
                          </td>
                          <td>{m.n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {fc.congestion_classification && (
                    <p className="mt-3 text-xs text-slate-400">
                      Congestion classifier at this horizon:{' '}
                      {fc.congestion_classification.model} · accuracy{' '}
                      {pct(fc.congestion_classification.accuracy)} ·
                      macro F1{' '}
                      {pct(fc.congestion_classification.f1_macro)}
                    </p>
                  )}
                </div>

              </div>
            )}
          </Card>

          {/* TRAINING INFORMATION */}

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

            <Card
              title="Training setup"
              subtitle="From models/model_metadata.json and forecast_metrics.json"
            >
              <div className="space-y-2 text-xs text-slate-400">
                {meta?.model_type && (
                  <p>
                    <span className="text-slate-300">Task:</span>{' '}
                    {meta.model_type}
                  </p>
                )}

                {meta?.split_strategy && (
                  <p>
                    <span className="text-slate-300">Split:</span>{' '}
                    {meta.split_strategy}
                  </p>
                )}

                {meta && (
                  <p>
                    <span className="text-slate-300">Rows:</span>{' '}
                    train {meta.train_rows} · validation{' '}
                    {meta.validation_rows} · test {meta.test_rows}
                  </p>
                )}

                {meta?.test_start && (
                  <p>
                    <span className="text-slate-300">Test window:</span>{' '}
                    {meta.test_start} → {meta.test_end}
                  </p>
                )}

                {meta?.runtime_versions && (
                  <p className="flex flex-wrap gap-1.5">
                    {Object.entries(meta.runtime_versions).map(
                      ([k, v]) => (
                        <Badge key={k} variant="neutral">
                          {k} {v}
                        </Badge>
                      )
                    )}
                  </p>
                )}

                {d.forecasting?.metadata.lstm_status && (
                  <p>
                    <span className="text-slate-300">LSTM:</span>{' '}
                    {d.forecasting.metadata.lstm_status}
                  </p>
                )}
              </div>
            </Card>

            <UnavailableNote>
              The backend has no{' '}
              {d.not_available
                .map((x) => x.replace('_', ' '))
                .join(', ')}{' '}
              artifacts. The LSTM / Prophet / ARIMA comparison,
              training-loss curve and feature-importance chart from
              the original design were demo values for models that do
              not exist in this project, so they were removed.
            </UnavailableNote>

          </div>

        </>
      )}

      {/* ======================================================
          EDGE-COST CALCULATION & INCIDENT IMPACT ANALYSIS
          TABLE 4.3 FROM PROJECT REPORT
      ====================================================== */}

      <Card
        title="Edge-Cost Calculation & Incident Impact Analysis"
        subtitle="Table 4.3 · Edge-cost components including incident effects"
        icon={<Route className="h-5 w-5" />}
      >

        <p className="mb-5 text-sm leading-6 text-slate-400">
          The routing engine calculates edge costs using road
          congestion, measured traffic flow, rainfall and incident
          conditions. Higher edge costs represent greater travel
          impedance, while confirmed road closures make the affected
          directed edge unavailable.
        </p>

        {/* EDGE COST TABLE */}

        <div className="overflow-x-auto rounded-lg border border-navy-700">
          <table className="w-full min-w-[650px] border-collapse text-left text-sm">

            <thead>
              <tr className="bg-navy-800 text-slate-200">
                <th className="w-[30%] border-b border-navy-700 px-4 py-3 font-semibold">
                  Component
                </th>

                <th className="border-b border-navy-700 px-4 py-3 font-semibold">
                  Effect on edge cost
                </th>
              </tr>
            </thead>

            <tbody>
              {EDGE_COST_COMPONENTS.map((item, index) => (
                <tr
                  key={item.component}
                  className={
                    index % 2 === 0
                      ? 'bg-navy-900/40'
                      : 'bg-navy-800/30'
                  }
                >
                  <td className="border-b border-navy-800 px-4 py-3 align-top font-medium text-cyan-300">
                    {item.component}
                  </td>

                  <td className="border-b border-navy-800 px-4 py-3 leading-6 text-slate-300">
                    {item.effect}
                  </td>
                </tr>
              ))}
            </tbody>

          </table>
        </div>

        {/* INCIDENT IMPACT EXPLANATION */}

        <div className="mt-7">
          <h3 className="mb-4 flex items-center gap-2 text-base font-semibold text-slate-100">
            <AlertTriangle className="h-5 w-5 text-amber-400" />
            How incident conditions affect routing
          </h3>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">

            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
              <div className="mb-2 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-400" />
                <span className="text-sm font-semibold text-amber-300">
                  Accidents
                </span>
              </div>

              <p className="text-sm leading-6 text-slate-400">
                Accident severity increases the edge-cost multiplier
                and adds an additional delay. The affected road is
                not automatically treated as completely closed.
              </p>
            </div>

            <div className="rounded-lg border border-orange-500/20 bg-orange-500/5 p-4">
              <div className="mb-2 flex items-center gap-2">
                <Route className="h-4 w-4 text-orange-400" />
                <span className="text-sm font-semibold text-orange-300">
                  Road work and obstructions
                </span>
              </div>

              <p className="text-sm leading-6 text-slate-400">
                Road work, obstructions and hazards increase
                the travel cost and introduce additional delay
                according to their severity.
              </p>
            </div>

            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
              <div className="mb-2 flex items-center gap-2">
                <Route className="h-4 w-4 text-red-400" />
                <span className="text-sm font-semibold text-red-300">
                  Road closures
                </span>
              </div>

              <p className="text-sm leading-6 text-slate-400">
                A confirmed road closure removes the affected
                directed edge from the routing graph. Dijkstra
                or A* must find another available path.
              </p>
            </div>

            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-4">
              <div className="mb-2 flex items-center gap-2">
                <CloudRain className="h-4 w-4 text-cyan-400" />
                <span className="text-sm font-semibold text-cyan-300">
                  Rainfall and traffic flow
                </span>
              </div>

              <p className="text-sm leading-6 text-slate-400">
                Rainfall increases edge cost when a rainfall
                value is supplied. Measured provider flow
                replaces the simulated congestion multiplier
                on matched road edges.
              </p>
            </div>

          </div>
        </div>

        {/* DATA INTERPRETATION */}

        <div className="mt-6 rounded-lg border border-navy-700 bg-navy-800/40 p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-200">
            Important distinction
          </h3>

          <p className="text-sm leading-6 text-slate-400">
            Simulated congestion conditions are used when
            requested. Measured provider flow uses traffic
            information received from a traffic-data provider
            on matched road segments. Confirmed closures are
            handled separately by removing the affected
            directed edge from the routing graph.
          </p>

          <div className="mt-3 flex flex-wrap gap-2">
            <Badge variant="neutral">Simulated congestion</Badge>
            <Badge variant="neutral">Measured provider flow</Badge>
            <Badge variant="neutral">Incident-based edge cost</Badge>
          </div>
        </div>

      </Card>

    </div>
  );
}
