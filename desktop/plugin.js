/*
THESIS: One native observatory connects session evidence to Hermes operations without becoming a separate dashboard.
OWN-WORLD: Hermes Desktop theme variables, SDK controls, codicons, compact borders, tabular data, and restrained status pills.
STORY: Find a session, verify its trace and accounting, then inspect runtime, profile, schedule, or Kanban health without leaving Hermes.
FIRST VIEWPORT: Native left navigation opens a compact header, four accounting signals, stable view tabs, and a dense evidence workspace.
FORM: Operate surface; evidence remains primary, progressive disclosure carries density, and redacted read-only data sets the trust boundary.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
*/
import {
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  Input,
  PALETTE_AREA,
  ROUTES_AREA,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  Skeleton,
  Textarea,
  compactNumber,
  host,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useState } from 'react'
import { Fragment, jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'session-lens'
const ROUTE = '/session-lens'

const color = {
  accent: 'var(--ui-accent)',
  accentSoft: 'color-mix(in srgb, var(--ui-accent) 14%, transparent)',
  danger: 'var(--destructive)',
  dangerSoft: 'color-mix(in srgb, var(--destructive) 12%, transparent)',
  primary: 'var(--ui-text-primary)',
  secondary: 'var(--ui-text-secondary)',
  tertiary: 'var(--ui-text-tertiary)',
  quaternary: 'var(--ui-text-quaternary)',
  stroke: 'var(--ui-stroke-secondary)',
  surface: 'var(--ui-bg-secondary)',
  surfaceRaised: 'var(--ui-bg-tertiary)'
}

const border = `1px solid ${color.stroke}`
const tabular = { fontVariantNumeric: 'tabular-nums' }
const timeOptions = [
  { id: '7', label: '7d' },
  { id: '30', label: '30d' },
  { id: '90', label: '90d' },
  { id: '0', label: 'All' }
]
const pageTabs = [
  { id: 'sessions', label: 'Sessions', codicon: 'list-tree' },
  { id: 'overview', label: 'Overview', codicon: 'graph' },
  { id: 'operations', label: 'Operations', codicon: 'pulse' },
  { id: 'tools', label: 'Tools', codicon: 'tools' },
  { id: 'skills', label: 'Skills', codicon: 'sparkle' },
  { id: 'system', label: 'System', codicon: 'server-environment' }
]

function apiPath(path, params = {}) {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&')
  return query ? `${path}?${query}` : path
}

function timestampDate(timestamp) {
  if (!timestamp) return null
  const numeric = Number(timestamp)
  const date = Number.isFinite(numeric)
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(timestamp)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatDate(timestamp) {
  const date = timestampDate(timestamp)
  if (!date) return 'Unknown time'
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short'
    }).format(date)
  } catch {
    return 'Unknown time'
  }
}

function formatShortDate(timestamp) {
  const date = timestampDate(timestamp)
  if (!date) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit'
    }).format(date)
  } catch {
    return '—'
  }
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.max(0, Number(seconds) || 0)
  if (total < 60) return `${Math.round(total)}s`
  if (total < 3600) return `${Math.round(total / 60)}m`
  const hours = Math.floor(total / 3600)
  const minutes = Math.round((total % 3600) / 60)
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

function formatCount(value) {
  const number = Number(value) || 0
  return compactNumber(number)
}

function formatCost(value, kind = 'unpriced') {
  if (kind === 'included') return 'Included'
  if (value === null || value === undefined || kind === 'unpriced') return 'Unpriced'
  const amount = Number(value) || 0
  if (amount === 0) return '$0.00'
  if (amount < 0.01) return `$${amount.toFixed(4)}`
  return `$${amount.toFixed(2)}`
}

function formatPercent(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function formatSeconds(value) {
  if (value === null || value === undefined) return '—'
  const seconds = Number(value) || 0
  return seconds < 10 ? `${seconds.toFixed(2)}s` : `${seconds.toFixed(1)}s`
}

function LoadingBlock({ rows = 4 }) {
  return jsx('div', {
    'aria-label': 'Loading',
    style: { display: 'grid', gap: '0.625rem', padding: '1rem' },
    children: Array.from({ length: rows }, (_, index) =>
      jsx(Skeleton, { style: { height: index === 0 ? '2.25rem' : '1.15rem', width: index % 2 ? '82%' : '100%' } }, index)
    )
  })
}

function ErrorBlock({ error, onRetry, title = 'Session Lens could not load this view' }) {
  const message = error?.message || String(error || 'The backend did not return data.')
  return jsx('div', {
    style: { display: 'grid', minHeight: '15rem', placeItems: 'center', padding: '2rem' },
    children: jsxs(ErrorState, {
      title,
      description: `${message} Enable session-lens in Hermes plugins and restart the gateway if this is the first install.`,
      children: [
        jsx(Button, { variant: 'outline', size: 'sm', onClick: onRetry, children: 'Try again' })
      ]
    })
  })
}

function Pill({ children, tone = 'neutral', title }) {
  const danger = tone === 'danger'
  const accent = tone === 'accent'
  return jsx('span', {
    title,
    style: {
      alignItems: 'center',
      background: danger ? color.dangerSoft : accent ? color.accentSoft : color.surfaceRaised,
      borderRadius: '999px',
      color: danger ? color.danger : accent ? color.accent : color.secondary,
      display: 'inline-flex',
      fontSize: '0.6875rem',
      fontWeight: 600,
      gap: '0.3rem',
      lineHeight: 1.5,
      maxWidth: '100%',
      padding: '0.1rem 0.45rem',
      whiteSpace: 'nowrap'
    },
    children
  })
}

function Metric({ label, value, detail, danger = false }) {
  return jsxs('div', {
    style: {
      borderRight: border,
      minWidth: 0,
      padding: '0.7rem 1rem'
    },
    children: [
      jsx('div', {
        style: {
          color: color.tertiary,
          fontSize: '0.6875rem',
          lineHeight: 1.35,
          marginBottom: '0.18rem'
        },
        children: label
      }),
      jsx('div', {
        style: {
          ...tabular,
          color: danger ? color.danger : color.primary,
          fontSize: '1rem',
          fontWeight: 650,
          lineHeight: 1.3,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap'
        },
        children: value
      }),
      detail
        ? jsx('div', {
            style: { color: color.quaternary, fontSize: '0.625rem', marginTop: '0.15rem' },
            children: detail
          })
        : null
    ]
  })
}

function StatStrip({ overview }) {
  const totals = overview?.totals || {}
  return jsx('div', {
    style: {
      borderBottom: border,
      borderTop: border,
      display: 'grid',
      gridTemplateColumns: 'repeat(4, minmax(7rem, 1fr))',
      overflowX: 'auto'
    },
    children: [
      jsx(Metric, {
        label: 'Recorded cost',
        value: overview ? formatCost(totals.display_cost_usd, totals.cost_kind) : '—',
        detail: overview ? `${formatCount(totals.unpriced_sessions)} unpriced sessions` : null
      }, 'cost'),
      jsx(Metric, {
        label: 'Tokens',
        value: overview ? formatCount(totals.total_tokens) : '—',
        detail: overview ? `${formatCount(totals.cache_read_tokens)} cache read` : null
      }, 'tokens'),
      jsx(Metric, {
        label: 'Sessions',
        value: overview ? formatCount(totals.sessions) : '—',
        detail: overview ? `${formatCount(totals.messages)} messages` : null
      }, 'sessions'),
      jsx(Metric, {
        label: 'Failures detected',
        value: overview ? formatCount(totals.failures) : '—',
        detail: overview ? `${formatCount(totals.tool_calls)} tool calls` : null,
        danger: Number(totals.failures) > 0
      }, 'failures')
    ]
  })
}

function SectionHeading({ title, description, action }) {
  return jsxs('div', {
    style: {
      alignItems: 'flex-start',
      display: 'flex',
      gap: '1rem',
      justifyContent: 'space-between',
      marginBottom: '0.85rem'
    },
    children: [
      jsxs('div', {
        style: { minWidth: 0 },
        children: [
          jsx('h2', {
            style: { color: color.primary, fontSize: '0.9375rem', fontWeight: 650, lineHeight: 1.35, margin: 0 },
            children: title
          }),
          description
            ? jsx('p', {
                style: { color: color.tertiary, fontSize: '0.75rem', lineHeight: 1.5, margin: '0.2rem 0 0' },
                children: description
              })
            : null
        ]
      }),
      action || null
    ]
  })
}

function NativeSelect({ value, onChange, label, children }) {
  return jsx('label', {
    style: { alignItems: 'center', color: color.tertiary, display: 'inline-flex', fontSize: '0.6875rem', gap: '0.4rem' },
    children: jsxs(Fragment, {
      children: [
        jsx('span', { children: label }),
        jsx('select', {
          value,
          onChange: event => onChange(event.target.value),
          style: {
            background: color.surfaceRaised,
            border,
            borderRadius: '4px',
            color: color.primary,
            font: 'inherit',
            outlineColor: color.accent,
            padding: '0.28rem 1.7rem 0.28rem 0.45rem'
          },
          children
        })
      ]
    })
  })
}

function CostLabel({ session }) {
  const kind = session.cost_kind || 'unpriced'
  return jsx(Pill, {
    tone: kind === 'actual' ? 'accent' : 'neutral',
    title: session.cost_source ? `Source: ${session.cost_source}` : `Cost: ${kind}`,
    children: formatCost(session.display_cost_usd, kind)
  })
}

function OutcomePill({ session }) {
  const outcome = session.outcome || 'closed'
  const tone = outcome === 'failed' ? 'danger' : outcome === 'running' || outcome === 'completed' ? 'accent' : 'neutral'
  const icon = outcome === 'failed' ? 'error' : outcome === 'running' ? 'record' : outcome === 'completed' ? 'pass' : 'circle-outline'
  return jsx(Pill, {
    tone,
    title: session.end_reason ? `Hermes end reason: ${session.end_reason}` : 'No end reason recorded',
    children: jsxs(Fragment, {
      children: [jsx(Codicon, { name: icon, size: '0.65rem' }), session.outcome_label || 'Closed']
    })
  })
}

function SessionRow({ session, selected, onSelect }) {
  return jsx('button', {
    type: 'button',
    onClick: onSelect,
    'aria-current': selected ? 'true' : undefined,
    style: {
      background: selected ? color.accentSoft : 'transparent',
      border: 'none',
      borderBottom: border,
      color: color.primary,
      cursor: 'pointer',
      display: 'grid',
      gap: '0.45rem',
      padding: '0.78rem 0.85rem',
      textAlign: 'left',
      width: '100%'
    },
    children: [
      jsxs('div', {
        style: { alignItems: 'flex-start', display: 'flex', gap: '0.6rem', justifyContent: 'space-between', minWidth: 0 },
        children: [
          jsxs('div', {
            style: { minWidth: 0 },
            children: [
              jsx('div', {
                style: {
                  fontSize: '0.8125rem',
                  fontWeight: selected ? 650 : 550,
                  lineHeight: 1.35,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap'
                },
                children: session.title
              }),
              jsx('div', {
                style: { color: color.quaternary, fontSize: '0.6875rem', marginTop: '0.15rem' },
                children: `${formatShortDate(session.last_activity_at || session.started_at)} · ${session.source || 'unknown'}`
              })
            ]
          }),
          jsxs('div', {
            style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.3rem', justifyContent: 'flex-end' },
            children: [
              jsx(OutcomePill, { session }),
              session.failure_count > 0
                ? jsx(Pill, {
                    tone: 'danger',
                    title: 'Detected failed tool results',
                    children: jsxs(Fragment, {
                      children: [jsx(Codicon, { name: 'warning', size: '0.65rem' }), String(session.failure_count)]
                    })
                  })
                : null
            ]
          })
        ]
      }),
      jsxs('div', {
        style: { alignItems: 'center', color: color.tertiary, display: 'flex', flexWrap: 'wrap', fontSize: '0.6875rem', gap: '0.35rem 0.7rem' },
        children: [
          jsx('span', { style: tabular, children: `${formatCount(session.total_tokens)} tokens` }),
          jsx('span', { style: tabular, children: `${formatCount(session.tool_call_count)} tools` }),
          jsx(CostLabel, { session })
        ]
      }),
      session.model
        ? jsx('div', {
            style: { color: color.quaternary, fontSize: '0.625rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
            children: session.model
          })
        : null,
      session.search_snippet
        ? jsx('div', {
            style: {
              borderTop: border,
              color: color.tertiary,
              fontSize: '0.6875rem',
              lineHeight: 1.45,
              marginTop: '0.1rem',
              paddingTop: '0.4rem'
            },
            children: session.search_snippet
          })
        : null
    ]
  })
}

function DetailMetricGrid({ session }) {
  const items = [
    ['Cost', formatCost(session.display_cost_usd, session.cost_kind), session.cost_kind],
    ['Tokens', formatCount(session.total_tokens), `${formatCount(session.input_tokens)} in · ${formatCount(session.output_tokens)} out`],
    ['Tools', formatCount(session.tool_call_count), `${formatCount(session.failure_count)} failures`],
    ['Duration', formatDuration(session.duration_seconds), `${formatCount(session.message_count)} messages`]
  ]
  return jsx('div', {
    style: { borderBottom: border, borderTop: border, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(7rem, 1fr))', overflowX: 'auto' },
    children: items.map(([label, value, detail]) => jsx(Metric, { label, value, detail, danger: label === 'Tools' && session.failure_count > 0 }, label))
  })
}

function SimpleTable({ columns, rows, emptyTitle = 'Nothing recorded', emptyDescription }) {
  if (!rows?.length) {
    return jsx(EmptyState, { title: emptyTitle, description: emptyDescription })
  }
  return jsx('div', {
    style: { border, borderRadius: '6px', overflowX: 'auto' },
    children: jsx('table', {
      style: { borderCollapse: 'collapse', fontSize: '0.75rem', minWidth: '34rem', width: '100%' },
      children: [
        jsx('thead', {
          children: jsx('tr', {
            children: columns.map(column =>
              jsx('th', {
                scope: 'col',
                style: {
                  background: color.surface,
                  borderBottom: border,
                  color: color.tertiary,
                  fontSize: '0.6875rem',
                  fontWeight: 600,
                  padding: '0.5rem 0.65rem',
                  textAlign: column.align || 'left',
                  whiteSpace: 'nowrap'
                },
                children: column.label
              }, column.key)
            )
          })
        }),
        jsx('tbody', {
          children: rows.map((row, rowIndex) =>
            jsx('tr', {
              children: columns.map(column =>
                jsx('td', {
                  style: {
                    ...tabular,
                    borderBottom: rowIndex === rows.length - 1 ? 'none' : border,
                    color: column.muted ? color.tertiary : color.primary,
                    padding: '0.58rem 0.65rem',
                    textAlign: column.align || 'left',
                    verticalAlign: 'top'
                  },
                  children: column.render ? column.render(row) : row[column.key]
                }, column.key)
              )
            }, row.id || row.name || row.model || rowIndex)
          )
        })
      ]
    })
  })
}

function SessionSummary({ detail }) {
  const session = detail.session
  const messageRows = Object.entries(detail.message_roles || {}).map(([name, count]) => ({ name, count }))
  return jsxs('div', {
    style: { display: 'grid', gap: '1.35rem', padding: '1rem' },
    children: [
      jsxs('section', {
        children: [
          jsx(SectionHeading, { title: 'Accounting provenance', description: 'Hermes-recorded usage split by model and auxiliary task.' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'model', label: 'Model' },
              { key: 'task', label: 'Task', muted: true, render: row => row.task || 'main' },
              { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
              { key: 'api_call_count', label: 'Calls', align: 'right', render: row => formatCount(row.api_call_count) },
              { key: 'cost', label: 'Cost', align: 'right', render: row => formatCost(row.display_cost_usd, row.cost_kind) }
            ],
            rows: detail.models,
            emptyTitle: 'No per-model accounting rows',
            emptyDescription: 'This session may predate per-model usage storage; the session totals above remain available.'
          })
        ]
      }),
      jsxs('section', {
        children: [
          jsx(SectionHeading, { title: 'Message composition', description: 'Active database rows by recorded role.' }),
          jsx('div', {
            style: { display: 'flex', flexWrap: 'wrap', gap: '0.45rem' },
            children: messageRows.map(row => jsx(Pill, { children: `${row.name} ${formatCount(row.count)}` }, row.name))
          })
        ]
      }),
      detail.skills?.length
        ? jsxs('section', {
            children: [
              jsx(SectionHeading, { title: 'Skills invoked', description: 'Only explicit skill tool calls count as evidence.' }),
              jsx('div', {
                style: { display: 'flex', flexWrap: 'wrap', gap: '0.45rem' },
                children: detail.skills.map(skill =>
                  jsx(Pill, {
                    tone: 'accent',
                    title: `${skill.view_count} views · ${skill.manage_count} management actions`,
                    children: `${skill.name} · ${skill.view_count + skill.manage_count}`
                  }, skill.name)
                )
              })
            ]
          })
        : null,
      detail.delegations?.length
        ? jsxs('section', {
            children: [
              jsx(SectionHeading, { title: 'Delegated work', description: 'Async delegations linked to this session.' }),
              jsx(SimpleTable, {
                columns: [
                  { key: 'delegation_id', label: 'Delegation', render: row => String(row.delegation_id).slice(0, 18) },
                  { key: 'state', label: 'State' },
                  { key: 'delivery_state', label: 'Delivery', muted: true },
                  { key: 'dispatched_at', label: 'Dispatched', render: row => formatShortDate(row.dispatched_at) }
                ],
                rows: detail.delegations
              })
            ]
          })
        : null,
      jsxs('section', {
        style: { borderTop: border, paddingTop: '0.9rem' },
        children: [
          jsx('div', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Outcome' }),
          jsxs('div', {
            style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.45rem', marginTop: '0.25rem' },
            children: [
              jsx(OutcomePill, { session }),
              session.end_reason
                ? jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: session.end_reason })
                : null
            ]
          }),
          jsx('div', { style: { color: color.tertiary, fontSize: '0.6875rem', marginTop: '0.75rem' }, children: 'Session ID' }),
          jsx('code', { style: { color: color.secondary, fontSize: '0.6875rem', overflowWrap: 'anywhere' }, children: session.id }),
          session.parent_session_id
            ? jsxs(Fragment, {
                children: [
                  jsx('div', { style: { color: color.tertiary, fontSize: '0.6875rem', marginTop: '0.6rem' }, children: 'Parent session' }),
                  jsx('code', { style: { color: color.secondary, fontSize: '0.6875rem', overflowWrap: 'anywhere' }, children: session.parent_session_id })
                ]
              })
            : null,
          jsx('div', { style: { color: color.tertiary, fontSize: '0.6875rem', marginTop: '0.6rem' }, children: 'Working directory' }),
          jsx('code', { style: { color: color.secondary, fontSize: '0.6875rem', overflowWrap: 'anywhere' }, children: session.cwd || 'Not recorded' })
        ]
      })
    ]
  })
}

function ToolEvents({ events }) {
  if (!events?.length) {
    return jsx(EmptyState, { title: 'No tool calls recorded', description: 'Hermes did not store tool-call evidence for this session.' })
  }
  return jsx('div', {
    style: { display: 'grid' },
    children: events.slice(0, 300).map((event, index) =>
      jsxs('div', {
        style: { borderBottom: border, display: 'grid', gap: '0.35rem', padding: '0.75rem 1rem' },
        children: [
          jsxs('div', {
            style: { alignItems: 'center', display: 'flex', gap: '0.5rem', justifyContent: 'space-between' },
            children: [
              jsxs('div', {
                style: { alignItems: 'center', display: 'flex', gap: '0.4rem', minWidth: 0 },
                children: [
                  jsx(Codicon, { name: event.failure ? 'error' : 'tools', size: '0.78rem' }),
                  jsx('strong', { style: { fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis' }, children: event.name })
                ]
              }),
              jsx(Pill, { tone: event.failure ? 'danger' : 'neutral', children: event.status })
            ]
          }),
          jsx('div', { style: { color: color.tertiary, fontSize: '0.6875rem', lineHeight: 1.45, overflowWrap: 'anywhere' }, children: event.argument_summary }),
          event.result_snippet
            ? jsx('pre', {
                style: {
                  background: color.surface,
                  borderRadius: '4px',
                  color: event.failure ? color.danger : color.secondary,
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.65625rem',
                  lineHeight: 1.45,
                  margin: '0.15rem 0 0',
                  maxHeight: '8rem',
                  overflow: 'auto',
                  padding: '0.55rem',
                  whiteSpace: 'pre-wrap'
                },
                children: event.result_snippet
              })
            : null,
          jsx('time', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: formatShortDate(event.timestamp) })
        ]
      }, event.call_id || `${event.name}-${event.timestamp}-${index}`)
    )
  })
}

function FailureInspector({ failures, detectedTotal = 0 }) {
  if (!failures?.length) {
    return jsx(EmptyState, {
      title: 'No failures detected',
      description: 'No recorded error state or conservative failure signature was found in the analyzed tool results.'
    })
  }
  return jsxs('div', {
    children: [
      jsx('div', {
        style: { background: color.dangerSoft, borderBottom: border, color: color.danger, fontSize: '0.6875rem', lineHeight: 1.5, padding: '0.65rem 1rem' },
        children: `${formatCount(failures.length)} shown of ${formatCount(detectedTotal || failures.length)} detected failed call${detectedTotal === 1 ? '' : 's'}. Review the recorded result before drawing conclusions.`
      }),
      jsx(ToolEvents, { events: failures })
    ]
  })
}

function FilesView({ files, truncated }) {
  if (!files?.length) {
    return jsx(EmptyState, {
      title: 'No file paths observed',
      description: 'Session Lens only reports paths found in recorded tool arguments or bounded command text.'
    })
  }
  return jsxs('div', {
    style: { display: 'grid' },
    children: [
      truncated
        ? jsx('div', {
            style: { borderBottom: border, color: color.tertiary, fontSize: '0.6875rem', padding: '0.6rem 1rem' },
            children: 'The event scan reached its safety limit. This file list may be partial.'
          })
        : null,
      ...files.map(file =>
        jsxs('div', {
          style: { alignItems: 'center', borderBottom: border, display: 'flex', gap: '0.65rem', padding: '0.65rem 1rem' },
          children: [
            jsx(Codicon, { name: file.action === 'modified' ? 'edit' : file.action === 'read' ? 'file' : 'link', size: '0.8rem' }),
            jsx('code', {
              style: { color: color.secondary, flex: 1, fontSize: '0.6875rem', overflowWrap: 'anywhere' },
              children: file.path
            }),
            jsx(Pill, { tone: file.action === 'modified' ? 'accent' : 'neutral', children: file.action })
          ]
        }, file.path)
      )
    ]
  })
}

function AskLens({ detail, ctx }) {
  const [question, setQuestion] = useState('What stands out in this session, and what should I improve next time?')
  const session = detail.session
  const prompt = useMemo(() => {
    const toolCounts = new Map()
    for (const event of detail.tools || []) toolCounts.set(event.name, (toolCounts.get(event.name) || 0) + 1)
    const topTools = [...toolCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12)
      .map(([name, count]) => `${name} (${count})`)
      .join(', ')
    const skills = (detail.skills || []).map(item => item.name).join(', ')
    const failures = (detail.failures || [])
      .slice(0, 8)
      .map(item => `- ${item.name}: ${item.result_snippet || item.status}`)
      .join('\n')
    return [
      'Analyze this Hermes session using only the telemetry below. Distinguish recorded facts from inferences and do not invent missing pricing or intent.',
      '',
      `Question: ${question.trim() || 'Summarize the session.'}`,
      '',
      `Session: ${session.title}`,
      `Session ID: ${session.id}`,
      `Model: ${session.model || 'not recorded'}`,
      `Tokens: ${session.total_tokens} total (${session.input_tokens} input, ${session.output_tokens} output, ${session.cache_read_tokens} cache read, ${session.cache_write_tokens} cache write)`,
      `Cost: ${formatCost(session.display_cost_usd, session.cost_kind)} (${session.cost_kind})`,
      `Messages: ${session.message_count}`,
      `Tool calls: ${session.tool_call_count}`,
      `Detected failures: ${session.failure_count}`,
      `Duration: ${formatDuration(session.duration_seconds)}`,
      `Top recorded tools: ${topTools || 'none'}`,
      `Recorded skills invoked: ${skills || 'none'}`,
      '',
      failures ? `Failure evidence (bounded and redacted):\n${failures}` : 'Failure evidence: none detected',
      '',
      'Return: (1) concise assessment, (2) cost/token observations, (3) failure analysis, (4) three concrete improvements. State any uncertainty.'
    ].join('\n')
  }, [detail, question])

  const copy = async openChat => {
    const copied = await ctx.os.writeClipboard(prompt)
    if (!copied) {
      host.notify({ kind: 'error', message: 'Could not copy the Ask Lens prompt.' })
      return
    }
    if (openChat) {
      host.newChat()
      host.notify({ kind: 'success', message: 'Ask Lens prompt copied. Paste it into the new chat.' })
    } else {
      host.notify({ kind: 'success', message: 'Ask Lens prompt copied.' })
    }
  }

  return jsxs('div', {
    style: { display: 'grid', gap: '0.85rem', padding: '1rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Ask Lens',
        description: 'Builds a session-grounded prompt locally. Nothing is uploaded by this plugin.'
      }),
      jsx('label', {
        style: { color: color.secondary, display: 'grid', fontSize: '0.75rem', gap: '0.35rem' },
        children: jsxs(Fragment, {
          children: [
            jsx('span', { children: 'What do you want Hermes to analyze?' }),
            jsx(Textarea, {
              value: question,
              onChange: event => setQuestion(event.target.value),
              rows: 3,
              maxLength: 500
            })
          ]
        })
      }),
      jsx('details', {
        children: [
          jsx('summary', { style: { color: color.tertiary, cursor: 'pointer', fontSize: '0.6875rem' }, children: 'Preview generated prompt' }),
          jsx('pre', {
            style: {
              background: color.surface,
              border,
              borderRadius: '5px',
              color: color.secondary,
              fontFamily: 'var(--font-mono)',
              fontSize: '0.65625rem',
              lineHeight: 1.5,
              marginTop: '0.5rem',
              maxHeight: '18rem',
              overflow: 'auto',
              padding: '0.7rem',
              whiteSpace: 'pre-wrap'
            },
            children: prompt
          })
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: '0.5rem' },
        children: [
          jsx(Button, { size: 'sm', onClick: () => copy(true), children: 'Copy and open new chat' }),
          jsx(Button, { variant: 'outline', size: 'sm', onClick: () => copy(false), children: 'Copy prompt' })
        ]
      })
    ]
  })
}

function TraceView({ ctx, sessionId, days }) {
  const [limit, setLimit] = useState(100)
  useEffect(() => setLimit(100), [sessionId])
  const traceQuery = useQuery({
    queryKey: [PLUGIN_ID, 'trace', sessionId, limit],
    queryFn: () => ctx.rest(apiPath(`/sessions/${encodeURIComponent(sessionId)}/trace`, { limit })),
    enabled: Boolean(sessionId),
    placeholderData: previous => previous
  })
  const telemetryQuery = useQuery({
    queryKey: [PLUGIN_ID, 'session-telemetry', sessionId, days],
    queryFn: () => ctx.rest(apiPath('/telemetry', { days, session_id: sessionId })),
    enabled: Boolean(sessionId),
    refetchInterval: 60_000
  })
  if (traceQuery.isLoading) return jsx(LoadingBlock, { rows: 9 })
  if (traceQuery.isError) return jsx(ErrorBlock, { error: traceQuery.error, onRetry: traceQuery.refetch, title: 'Session trace unavailable' })
  const data = traceQuery.data
  const runtime = telemetryQuery.data?.summary
  const kindMeta = {
    user: ['account', 'User'],
    assistant: ['hubot', 'Assistant'],
    reasoning: ['lightbulb', 'Reasoning'],
    tool_call: ['tools', 'Tool call'],
    tool_result: ['terminal', 'Tool result']
  }
  return jsxs('div', {
    style: { display: 'grid' },
    children: [
      runtime
        ? jsx('div', {
            style: { borderBottom: border, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(7rem, 1fr))', overflowX: 'auto' },
            children: [
              jsx(Metric, { label: 'API calls in logs', value: formatCount(runtime.api_calls), detail: 'Session-attributed' }, 'calls'),
              jsx(Metric, { label: 'Median latency', value: formatSeconds(runtime.latency_p50_seconds), detail: `p95 ${formatSeconds(runtime.latency_p95_seconds)}` }, 'latency'),
              jsx(Metric, { label: 'Cache hit ratio', value: formatPercent(runtime.cache_hit_ratio), detail: `${formatCount(runtime.cache_read_tokens)} cache read` }, 'cache'),
              jsx(Metric, { label: 'Timed tool runs', value: formatCount(runtime.tool_runs), detail: 'From local agent logs' }, 'tools')
            ]
          })
        : null,
      jsx('div', {
        style: { background: color.surface, borderBottom: border, color: color.tertiary, fontSize: '0.6875rem', lineHeight: 1.5, padding: '0.55rem 1rem' },
        children: 'Chronological active-message trace. System and scheduled-job prompts are excluded; recorded content is secret-redacted and bounded to 6,000 characters per event.'
      }),
      data.events?.length
        ? jsx('ol', {
            style: { listStyle: 'none', margin: 0, padding: 0 },
            children: data.events.map(event => {
              const [icon, label] = kindMeta[event.kind] || ['circle-outline', event.kind]
              const failed = event.status === 'failed'
              return jsx('li', {
                style: { borderBottom: border, display: 'grid', gap: '0.45rem', padding: '0.8rem 1rem' },
                children: [
                  jsxs('div', {
                    style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.4rem', justifyContent: 'space-between' },
                    children: [
                      jsxs('div', {
                        style: { alignItems: 'center', display: 'flex', gap: '0.4rem', minWidth: 0 },
                        children: [
                          jsx(Codicon, { name: failed ? 'error' : icon, size: '0.78rem' }),
                          jsx('strong', { style: { fontSize: '0.75rem', fontWeight: 650 }, children: event.tool_name || label }),
                          event.kind === 'tool_call' ? jsx(Pill, { children: label }) : null,
                          failed ? jsx(Pill, { tone: 'danger', children: 'Failed' }) : null
                        ]
                      }),
                      jsx('time', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: formatShortDate(event.timestamp) })
                    ]
                  }),
                  event.kind === 'reasoning'
                    ? jsxs('details', {
                        children: [
                          jsx('summary', { style: { color: color.tertiary, cursor: 'pointer', fontSize: '0.6875rem' }, children: 'Show recorded reasoning' }),
                          jsx('pre', {
                            style: { background: color.surface, borderRadius: '5px', color: color.secondary, fontFamily: 'var(--font-mono)', fontSize: '0.6875rem', lineHeight: 1.55, margin: '0.45rem 0 0', maxHeight: '24rem', overflow: 'auto', padding: '0.65rem', whiteSpace: 'pre-wrap' },
                            children: event.content
                          })
                        ]
                      })
                    : jsx(event.kind === 'tool_result' ? 'pre' : 'div', {
                        style: {
                          background: event.kind === 'tool_result' ? color.surface : 'transparent',
                          borderRadius: event.kind === 'tool_result' ? '5px' : 0,
                          color: failed ? color.danger : color.secondary,
                          fontFamily: event.kind === 'tool_result' ? 'var(--font-mono)' : 'inherit',
                          fontSize: '0.71875rem',
                          lineHeight: 1.55,
                          margin: 0,
                          maxHeight: event.kind === 'tool_result' ? '24rem' : 'none',
                          overflow: event.kind === 'tool_result' ? 'auto' : 'visible',
                          overflowWrap: 'anywhere',
                          padding: event.kind === 'tool_result' ? '0.65rem' : 0,
                          whiteSpace: 'pre-wrap'
                        },
                        children: event.content || 'No display content recorded.'
                      })
                ]
              }, event.id)
            })
          })
        : jsx(EmptyState, { title: 'No trace events', description: 'No active user, assistant, reasoning, or tool rows were recorded.' }),
      data.pagination?.has_more
        ? jsx('div', {
            style: { padding: '0.75rem 1rem' },
            children: jsx(Button, {
              variant: 'outline',
              size: 'sm',
              disabled: traceQuery.isFetching || limit >= 200,
              onClick: () => setLimit(value => Math.min(200, value + 100)),
              children: limit >= 200 ? '200-message safety limit reached' : 'Load 100 more messages'
            })
          })
        : null
    ]
  })
}

function SessionDetail({ query, detailTab, setDetailTab, ctx, days, onBack }) {
  if (!query) {
    return jsx(EmptyState, { title: 'Choose a session', description: 'Select a session to inspect its recorded evidence.' })
  }
  if (query.isLoading) return jsx(LoadingBlock, { rows: 7 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Session detail unavailable' })
  const detail = query.data
  const session = detail.session
  const detailOptions = [
    { id: 'summary', label: 'Summary' },
    { id: 'trace', label: 'Trace' },
    { id: 'tools', label: `Tools ${detail.tools?.length || 0}` },
    { id: 'failures', label: `Failures ${session.failure_count || 0}` },
    { id: 'files', label: `Files ${detail.files?.length || 0}` },
    { id: 'ask', label: 'Ask Lens' }
  ]
  let content = jsx(SessionSummary, { detail })
  if (detailTab === 'trace') content = jsx(TraceView, { ctx, sessionId: session.id, days })
  if (detailTab === 'tools') content = jsx(ToolEvents, { events: detail.tools })
  if (detailTab === 'failures') content = jsx(FailureInspector, { failures: detail.failures, detectedTotal: session.failure_count })
  if (detailTab === 'files') content = jsx(FilesView, { files: detail.files, truncated: detail.analysis?.truncated })
  if (detailTab === 'ask') content = jsx(AskLens, { detail, ctx })

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 },
    children: [
      jsxs('header', {
        style: { alignItems: 'flex-start', display: 'flex', gap: '1rem', justifyContent: 'space-between', padding: '0.9rem 1rem' },
        children: [
          jsxs('div', {
            style: { alignItems: 'flex-start', display: 'flex', gap: '0.55rem', minWidth: 0 },
            children: [
              onBack
                ? jsx(Button, {
                    variant: 'ghost',
                    size: 'icon-xs',
                    onClick: onBack,
                    'aria-label': 'Back to sessions',
                    title: 'Back to sessions',
                    children: jsx(Codicon, { name: 'arrow-left' })
                  })
                : null,
              jsxs('div', {
                style: { minWidth: 0 },
                children: [
                  jsx('h2', {
                    style: { color: color.primary, fontSize: '1rem', fontWeight: 650, lineHeight: 1.35, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis' },
                    children: session.title
                  }),
                  jsx('div', {
                    style: { color: color.tertiary, fontSize: '0.6875rem', marginTop: '0.2rem' },
                    children: `${formatDate(session.started_at)} · ${session.model || 'model not recorded'}`
                  })
                ]
              })
            ]
          }),
          jsx(Button, {
            variant: 'outline',
            size: 'xs',
            onClick: () => host.openSession(session.id, { intent: 'stack' }),
            children: jsxs(Fragment, { children: [jsx(Codicon, { name: 'go-to-file' }), 'Open session'] })
          })
        ]
      }),
      jsx(DetailMetricGrid, { session }),
      jsx('div', {
        style: { borderBottom: border, overflowX: 'auto', padding: '0.55rem 1rem' },
        children: jsx(SegmentedControl, { options: detailOptions, value: detailTab, onChange: setDetailTab })
      }),
      jsx('div', { style: { flex: 1, minHeight: 0, overflow: 'auto' }, children: content })
    ]
  })
}

function SessionsView({ ctx, days, narrow }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('failures')
  const [failuresOnly, setFailuresOnly] = useState(false)
  const [limit, setLimit] = useState(50)
  const [selected, setSelected] = useState(null)
  const [detailTab, setDetailTab] = useState('summary')
  const [narrowPane, setNarrowPane] = useState('list')
  const debouncedSearch = useDebounced(search, 250)

  useEffect(() => setLimit(50), [debouncedSearch, sort, failuresOnly, days])
  useEffect(() => {
    if (narrow) setNarrowPane('list')
  }, [narrow])

  const listQuery = useQuery({
    queryKey: [PLUGIN_ID, 'sessions', days, debouncedSearch, sort, failuresOnly, limit],
    queryFn: () =>
      ctx.rest(apiPath('/sessions', {
        days,
        q: debouncedSearch,
        sort,
        failures_only: failuresOnly,
        limit
      })),
    placeholderData: previous => previous,
    refetchInterval: 60_000
  })
  const sessions = listQuery.data?.sessions || []

  useEffect(() => {
    if (!sessions.length) {
      setSelected(null)
      return
    }
    if (!selected || !sessions.some(item => item.id === selected)) {
      setSelected(sessions[0].id)
      setDetailTab('summary')
    }
  }, [sessions, selected])

  const detailQuery = useQuery({
    queryKey: [PLUGIN_ID, 'session', selected],
    queryFn: () => ctx.rest(`/sessions/${encodeURIComponent(selected)}`),
    enabled: Boolean(selected),
    refetchInterval: 30_000
  })
  const pagination = listQuery.data?.pagination

  return jsxs('div', {
    style: { display: 'flex', flex: 1, flexDirection: 'column', minHeight: 0 },
    children: [
      jsxs('div', {
        style: { alignItems: 'center', borderBottom: border, display: narrow && narrowPane === 'detail' ? 'none' : 'flex', flexWrap: 'wrap', gap: '0.5rem', padding: '0.65rem 0.8rem' },
        children: [
          jsx('div', {
            style: { flex: '1 1 16rem', minWidth: '12rem' },
            children: jsx(Input, {
              type: 'search',
              value: search,
              onChange: event => setSearch(event.target.value),
              placeholder: 'Search sessions, paths, models, and message text…',
              'aria-label': 'Search sessions'
            })
          }),
          jsx(NativeSelect, {
            label: 'Sort',
            value: sort,
            onChange: setSort,
            children: [
              jsx('option', { value: 'failures', children: 'Failures first' }),
              jsx('option', { value: 'recent', children: 'Recent' }),
              jsx('option', { value: 'cost', children: 'Cost' }),
              jsx('option', { value: 'tokens', children: 'Tokens' }),
              jsx('option', { value: 'tools', children: 'Tool calls' })
            ]
          }),
          jsx(Button, {
            variant: failuresOnly ? 'secondary' : 'outline',
            size: 'xs',
            'aria-pressed': failuresOnly,
            onClick: () => setFailuresOnly(value => !value),
            children: jsxs(Fragment, { children: [jsx(Codicon, { name: 'error' }), 'Failures only'] })
          })
        ]
      }),
      listQuery.isError
        ? jsx(ErrorBlock, { error: listQuery.error, onRetry: listQuery.refetch })
        : jsx('div', {
            style: {
              display: 'grid',
              flex: 1,
              gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'minmax(20rem, 0.82fr) minmax(28rem, 1.38fr)',
              gridTemplateRows: 'minmax(0, 1fr)',
              minHeight: 0,
              overflow: 'hidden'
            },
            children: [
              jsxs('aside', {
                'aria-label': 'Sessions',
                style: { borderRight: narrow ? 'none' : border, display: narrow && narrowPane !== 'list' ? 'none' : 'flex', flexDirection: 'column', minHeight: 0 },
                children: [
                  jsxs('div', {
                    style: { alignItems: 'center', borderBottom: border, color: color.tertiary, display: 'flex', fontSize: '0.6875rem', justifyContent: 'space-between', padding: '0.45rem 0.85rem' },
                    children: [
                      jsx('span', { children: pagination ? `${formatCount(pagination.total)} sessions` : 'Loading sessions…' }),
                      listQuery.isFetching ? jsx(Codicon, { name: 'sync~spin', size: '0.7rem' }) : null
                    ]
                  }),
                  listQuery.isLoading
                    ? jsx(LoadingBlock, { rows: 6 })
                    : sessions.length
                      ? jsx('div', {
                          style: { flex: 1, minHeight: 0, overflow: 'auto' },
                          children: sessions.map(session =>
                            jsx(SessionRow, {
                              session,
                              selected: session.id === selected,
                              onSelect: () => {
                                setSelected(session.id)
                                setDetailTab('summary')
                                if (narrow) setNarrowPane('detail')
                              }
                            }, session.id)
                          )
                        })
                      : jsx(EmptyState, {
                          title: 'No matching sessions',
                          description: search ? 'Try a broader search or a longer time range.' : 'No sessions were recorded in this time range.'
                        }),
                  pagination?.has_more
                    ? jsx('div', {
                        style: { borderTop: border, padding: '0.55rem 0.75rem' },
                        children: jsx(Button, {
                          variant: 'outline',
                          size: 'xs',
                          disabled: listQuery.isFetching || limit >= 500,
                          onClick: () => setLimit(value => Math.min(500, value + 50)),
                          style: { width: '100%' },
                          children: limit >= 500 ? '500-session safety limit reached' : 'Load 50 more'
                        })
                      })
                    : null
                ]
              }),
              jsx('main', {
                style: { display: narrow && narrowPane !== 'detail' ? 'none' : 'block', minHeight: 0, overflow: 'hidden' },
                children: selected
                  ? jsx(SessionDetail, {
                      query: detailQuery,
                      detailTab,
                      setDetailTab,
                      ctx,
                      days,
                      onBack: narrow ? () => setNarrowPane('list') : undefined
                    })
                  : jsx(EmptyState, { title: 'Choose a session', description: 'Session evidence will appear here.' })
              })
            ]
          })
    ]
  })
}

function useDebounced(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timeout = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timeout)
  }, [value, delay])
  return debounced
}

function DailyBars({ rows }) {
  if (!rows?.length) return jsx(EmptyState, { title: 'No daily activity', description: 'No sessions were recorded in this period.' })
  const max = Math.max(...rows.map(row => Number(row.total_tokens) || 0), 1)
  return jsx('div', {
    style: { alignItems: 'end', borderBottom: border, display: 'flex', gap: '0.25rem', height: '11rem', overflowX: 'auto', padding: '0.75rem 0.25rem 0' },
    children: rows.map(row => {
      const height = Math.max(3, ((Number(row.total_tokens) || 0) / max) * 100)
      return jsxs('div', {
        title: `${row.day}: ${formatCount(row.total_tokens)} tokens · ${row.sessions} sessions · ${formatCost(row.cost_usd, 'estimated')}`,
        style: { alignItems: 'center', display: 'flex', flex: '1 0 0.8rem', flexDirection: 'column', height: '100%', justifyContent: 'end', minWidth: '0.55rem' },
        children: [
          jsx('div', {
            style: { background: color.accent, borderRadius: '2px 2px 0 0', height: `${height}%`, minHeight: '3px', opacity: 0.75, width: '100%' }
          }),
          jsx('span', {
            style: { color: color.quaternary, fontSize: '0.5625rem', marginTop: '0.25rem', writingMode: rows.length > 18 ? 'vertical-rl' : 'horizontal-tb' },
            children: row.day.slice(5)
          })
        ]
      }, row.day)
    })
  })
}

function OverviewView({ query }) {
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Overview unavailable' })
  const data = query.data
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1.5rem', margin: '0 auto', maxWidth: '84rem' },
      children: [
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Usage over time', description: 'Total recorded input, output, and cache tokens by local calendar day.' }),
            jsx(DailyBars, { rows: data.daily })
          ]
        }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Models', description: 'Per-model usage rows include auxiliary work such as compression and title generation.' }),
            jsx(SimpleTable, {
              columns: [
                { key: 'model', label: 'Model' },
                { key: 'billing_provider', label: 'Provider', muted: true, render: row => row.billing_provider || 'unknown' },
                { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
                { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
                { key: 'cost_usd', label: 'Recorded cost', align: 'right', render: row => formatCost(row.cost_usd, row.cost_kind) }
              ],
              rows: data.models,
              emptyTitle: 'No model usage rows'
            })
          ]
        }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Session outcomes', description: 'Conservative classification based on the recorded Hermes end reason.' }),
            jsx(SimpleTable, {
              columns: [
                { key: 'outcome', label: 'Outcome', render: row => jsx(Pill, { tone: row.outcome === 'failed' ? 'danger' : row.outcome === 'running' || row.outcome === 'completed' ? 'accent' : 'neutral', children: row.outcome.charAt(0).toUpperCase() + row.outcome.slice(1) }) },
                { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) }
              ],
              rows: data.outcomes,
              emptyTitle: 'No session outcomes'
            })
          ]
        }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Session sources', description: 'Where sessions entered Hermes.' }),
            jsx(SimpleTable, {
              columns: [
                { key: 'source', label: 'Source' },
                { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
                { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
                { key: 'tool_calls', label: 'Tool calls', align: 'right', render: row => formatCount(row.tool_calls) }
              ],
              rows: data.sources,
              emptyTitle: 'No session sources'
            })
          ]
        })
      ]
    })
  })
}

function ToolsView({ ctx, days }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'tools', days],
    queryFn: () => ctx.rest(apiPath('/tools', { days })),
    refetchInterval: 60_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Tool analytics unavailable' })
  const data = query.data
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { margin: '0 auto', maxWidth: '78rem' },
      children: [
        jsx(SectionHeading, {
          title: 'Tool reliability',
          description: `${formatCount(data.totals.calls)} recorded calls across ${formatCount(data.totals.distinct_tools)} tools. Failure signatures are conservative and inspectable per session.`
        }),
        jsx(SimpleTable, {
          columns: [
            { key: 'name', label: 'Tool' },
            { key: 'calls', label: 'Calls', align: 'right', render: row => formatCount(row.calls) },
            { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
            {
              key: 'failures',
              label: 'Failures',
              align: 'right',
              render: row => row.failures ? jsx(Pill, { tone: 'danger', children: formatCount(row.failures) }) : '0'
            },
            { key: 'failure_rate', label: 'Failure rate', align: 'right', render: row => `${(Number(row.failure_rate) * 100).toFixed(1)}%` },
            { key: 'last_used_at', label: 'Last used', render: row => formatShortDate(row.last_used_at), muted: true }
          ],
          rows: data.tools,
          emptyTitle: 'No tool calls recorded',
          emptyDescription: 'The selected period contains no tool-call evidence.'
        }),
        data.truncated
          ? jsx('p', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'The aggregate scan reached its 50,000-row safety limit.' })
          : null
      ]
    })
  })
}

function SkillsViewPanel({ ctx, days }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'skills', days],
    queryFn: () => ctx.rest(apiPath('/skills', { days })),
    refetchInterval: 60_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Skill analytics unavailable' })
  const data = query.data
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { margin: '0 auto', maxWidth: '72rem' },
      children: [
        jsx(SectionHeading, {
          title: 'Skills actually invoked',
          description: data.definition
        }),
        jsx(SimpleTable, {
          columns: [
            { key: 'name', label: 'Skill' },
            { key: 'view_count', label: 'Loads', align: 'right', render: row => formatCount(row.view_count) },
            { key: 'manage_count', label: 'Management', align: 'right', render: row => formatCount(row.manage_count) },
            { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
            { key: 'last_used_at', label: 'Last invoked', render: row => formatShortDate(row.last_used_at), muted: true }
          ],
          rows: data.skills,
          emptyTitle: 'No explicit skill invocations',
          emptyDescription: 'Available or loaded skills are intentionally not presented as used.'
        })
      ]
    })
  })
}

function RuntimeHealth({ ctx, days }) {
  const telemetryQuery = useQuery({
    queryKey: [PLUGIN_ID, 'telemetry', days],
    queryFn: () => ctx.rest(apiPath('/telemetry', { days })),
    refetchInterval: 60_000
  })
  const gatewayQuery = useQuery({
    queryKey: [PLUGIN_ID, 'gateway'],
    queryFn: () => ctx.rest('/gateway'),
    refetchInterval: 30_000
  })
  if (telemetryQuery.isLoading || gatewayQuery.isLoading) return jsx(LoadingBlock, { rows: 9 })
  if (telemetryQuery.isError) return jsx(ErrorBlock, { error: telemetryQuery.error, onRetry: telemetryQuery.refetch, title: 'Runtime telemetry unavailable' })
  if (gatewayQuery.isError) return jsx(ErrorBlock, { error: gatewayQuery.error, onRetry: gatewayQuery.refetch, title: 'Gateway health unavailable' })
  const telemetry = telemetryQuery.data
  const summary = telemetry.summary
  const gateways = gatewayQuery.data.gateways || []
  return jsxs('div', {
    style: { display: 'grid', gap: '1.5rem' },
    children: [
      jsx('div', {
        style: { borderBottom: border, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(7rem, 1fr))', overflowX: 'auto' },
        children: [
          jsx(Metric, { label: 'API calls in logs', value: formatCount(summary.api_calls), detail: `${formatCount(telemetry.models.length)} models` }, 'calls'),
          jsx(Metric, { label: 'Median latency', value: formatSeconds(summary.latency_p50_seconds), detail: `p95 ${formatSeconds(summary.latency_p95_seconds)}` }, 'latency'),
          jsx(Metric, { label: 'Cache hit ratio', value: formatPercent(summary.cache_hit_ratio), detail: `${formatCount(summary.cache_read_tokens)} tokens read` }, 'cache'),
          jsx(Metric, { label: 'Timed tool runs', value: formatCount(summary.tool_runs), detail: `${formatCount(telemetry.tools.reduce((count, item) => count + item.failed, 0))} failed`, danger: telemetry.tools.some(item => item.failed) }, 'tools')
        ]
      }),
      jsxs('section', {
        children: [
          jsx(SectionHeading, { title: 'Gateway and platforms', description: 'Current local gateway state for the default and named Hermes profiles.' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'profile', label: 'Profile' },
              { key: 'state', label: 'Gateway', render: row => jsx(Pill, { tone: row.state === 'running' ? 'accent' : 'neutral', children: row.state }) },
              { key: 'platforms', label: 'Platforms', render: row => row.platforms?.length ? row.platforms.map(item => `${item.name}: ${item.state}`).join(' · ') : 'None recorded' },
              { key: 'active_agents', label: 'Agents', align: 'right', render: row => formatCount(row.active_agents) },
              { key: 'updated_at', label: 'Updated', render: row => formatShortDate(row.updated_at), muted: true }
            ],
            rows: gateways,
            emptyTitle: 'No gateway state files',
            emptyDescription: 'Hermes has not written gateway status for these profiles.'
          })
        ]
      }),
      jsxs('section', {
        children: [
          jsx(SectionHeading, { title: 'Model latency and cache', description: 'Parsed from bounded local Hermes agent logs and cached until a log file changes.' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'model', label: 'Model' },
              { key: 'api_calls', label: 'Calls', align: 'right', render: row => formatCount(row.api_calls) },
              { key: 'latency_p50_seconds', label: 'p50', align: 'right', render: row => formatSeconds(row.latency_p50_seconds) },
              { key: 'latency_p95_seconds', label: 'p95', align: 'right', render: row => formatSeconds(row.latency_p95_seconds) },
              { key: 'cache_hit_ratio', label: 'Cache hit', align: 'right', render: row => formatPercent(row.cache_hit_ratio) }
            ],
            rows: telemetry.models,
            emptyTitle: 'No API timing rows',
            emptyDescription: 'The selected log window contains no recognized Hermes API metrics.'
          })
        ]
      }),
      jsxs('section', {
        children: [
          jsx(SectionHeading, { title: 'Tool duration', description: 'Executor timings complement the database-backed reliability view.' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'tool', label: 'Tool' },
              { key: 'runs', label: 'Runs', align: 'right', render: row => formatCount(row.runs) },
              { key: 'duration_avg_seconds', label: 'Average', align: 'right', render: row => formatSeconds(row.duration_avg_seconds) },
              { key: 'duration_p95_seconds', label: 'p95', align: 'right', render: row => formatSeconds(row.duration_p95_seconds) },
              { key: 'failed', label: 'Failed', align: 'right', render: row => row.failed ? jsx(Pill, { tone: 'danger', children: formatCount(row.failed) }) : '0' },
              { key: 'cancelled', label: 'Cancelled', align: 'right', render: row => formatCount(row.cancelled) }
            ],
            rows: telemetry.tools,
            emptyTitle: 'No timed tool runs',
            emptyDescription: 'The selected log window contains no recognized executor metrics.'
          })
        ]
      })
    ]
  })
}

function ProfilesView({ ctx, days }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'profiles', days],
    queryFn: () => ctx.rest(apiPath('/profiles', { days })),
    refetchInterval: 60_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Profile analytics unavailable' })
  const data = query.data
  return jsxs('div', {
    style: { display: 'grid', gap: '1rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Hermes profiles',
        description: `${formatCount(data.totals.sessions)} sessions across ${formatCount(data.totals.profiles)} read-only profile stores in this period.`
      }),
      jsx(SimpleTable, {
        columns: [
          { key: 'name', label: 'Profile', render: row => row.is_default ? `${row.name} · root` : row.name },
          { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
          { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
          { key: 'recorded_cost_usd', label: 'Recorded cost', align: 'right', render: row => formatCost(row.recorded_cost_usd, 'estimated') },
          { key: 'running', label: 'Running', align: 'right', render: row => formatCount(row.outcomes?.running) },
          { key: 'open', label: 'Open', align: 'right', render: row => formatCount(row.outcomes?.open) },
          { key: 'failed', label: 'Failed', align: 'right', render: row => row.outcomes?.failed ? jsx(Pill, { tone: 'danger', children: formatCount(row.outcomes.failed) }) : '0' },
          { key: 'last_activity_at', label: 'Last activity', render: row => formatShortDate(row.last_activity_at), muted: true }
        ],
        rows: data.profiles,
        emptyTitle: 'No profile stores found'
      })
    ]
  })
}

function SchedulesView({ ctx }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'schedules'],
    queryFn: () => ctx.rest('/schedules'),
    refetchInterval: 30_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Schedules unavailable' })
  const data = query.data
  return jsxs('div', {
    style: { display: 'grid', gap: '1rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Scheduled jobs',
        description: `${formatCount(data.totals.enabled)} of ${formatCount(data.totals.jobs)} jobs enabled. Prompts are intentionally excluded.`
      }),
      jsx(SimpleTable, {
        columns: [
          { key: 'name', label: 'Job' },
          { key: 'profile', label: 'Profile', muted: true },
          { key: 'enabled', label: 'State', render: row => jsx(Pill, { tone: row.enabled ? 'accent' : 'neutral', children: row.enabled ? 'Enabled' : 'Disabled' }) },
          { key: 'schedule', label: 'Schedule' },
          { key: 'next_run_at', label: 'Next run', render: row => formatShortDate(row.next_run_at) },
          { key: 'last_status', label: 'Last status', render: row => row.last_error ? jsx(Pill, { tone: 'danger', title: row.last_error, children: row.last_status || 'Error' }) : row.last_status || '—' },
          { key: 'failure_streak', label: 'Failures', align: 'right', render: row => formatCount(row.failure_streak) }
        ],
        rows: data.schedules,
        emptyTitle: 'No scheduled jobs',
        emptyDescription: 'No cron job metadata was found in the default or named profiles.'
      })
    ]
  })
}

function KanbanView({ ctx }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'kanban'],
    queryFn: () => ctx.rest('/kanban'),
    refetchInterval: 30_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Kanban operations unavailable' })
  const data = query.data
  const tasks = (data.boards || []).flatMap(board => board.tasks.map(task => ({ ...task, id: `${board.name}:${task.id}`, board: board.name })))
  return jsxs('div', {
    style: { display: 'grid', gap: '1rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Kanban execution',
        description: `${formatCount(data.totals.tasks)} tasks and ${formatCount(data.totals.runs)} execution runs across ${formatCount(data.totals.boards)} boards.`
      }),
      jsx(SimpleTable, {
        columns: [
          { key: 'title', label: 'Task' },
          { key: 'board', label: 'Board', muted: true },
          { key: 'status', label: 'Status', render: row => jsx(Pill, { tone: row.status === 'done' || row.status === 'completed' ? 'accent' : row.consecutive_failures ? 'danger' : 'neutral', children: row.status }) },
          { key: 'assignee', label: 'Assignee', render: row => row.assignee || '—' },
          { key: 'priority', label: 'Priority', align: 'right' },
          { key: 'consecutive_failures', label: 'Failures', align: 'right', render: row => row.consecutive_failures ? jsx(Pill, { tone: 'danger', title: row.last_failure_error, children: formatCount(row.consecutive_failures) }) : '0' },
          { key: 'completed_at', label: 'Updated', render: row => formatShortDate(row.completed_at || row.started_at || row.created_at), muted: true }
        ],
        rows: tasks,
        emptyTitle: 'No Kanban tasks',
        emptyDescription: 'No tasks were found in the shared Hermes Kanban stores.'
      })
    ]
  })
}

function OperationsView({ ctx, days }) {
  const [section, setSection] = useState('health')
  const options = [
    { id: 'health', label: 'Health' },
    { id: 'profiles', label: 'Profiles' },
    { id: 'schedules', label: 'Schedules' },
    { id: 'kanban', label: 'Kanban' }
  ]
  let content = jsx(RuntimeHealth, { ctx, days })
  if (section === 'profiles') content = jsx(ProfilesView, { ctx, days })
  if (section === 'schedules') content = jsx(SchedulesView, { ctx })
  if (section === 'kanban') content = jsx(KanbanView, { ctx })
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1.25rem', margin: '0 auto', maxWidth: '84rem' },
      children: [
        jsxs('div', {
          style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.75rem', justifyContent: 'space-between' },
          children: [
            jsx(SectionHeading, { title: 'Operations', description: 'Runtime health and work orchestration across Hermes profiles.' }),
            jsx(SegmentedControl, { options, value: section, onChange: setSection })
          ]
        }),
        content
      ]
    })
  })
}

function DefinitionList({ rows }) {
  return jsx('dl', {
    style: { borderTop: border, margin: 0 },
    children: rows.flatMap(([term, description]) => [
      jsx('dt', {
        style: { borderBottom: border, color: color.tertiary, fontSize: '0.6875rem', fontWeight: 600, padding: '0.65rem 0.75rem' },
        children: term
      }, `${term}-term`),
      jsx('dd', {
        style: { ...tabular, borderBottom: border, color: color.primary, fontSize: '0.75rem', margin: 0, overflowWrap: 'anywhere', padding: '0.65rem 0.75rem' },
        children: description
      }, `${term}-value`)
    ])
  })
}

function SystemView({ ctx }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'system'],
    queryFn: () => ctx.rest('/system'),
    refetchInterval: 60_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'System information unavailable' })
  const data = query.data
  const db = data.database
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1.5rem', margin: '0 auto', maxWidth: '68rem' },
      children: [
        jsxs('section', {
          children: [
            jsx(SectionHeading, {
              title: 'Data source',
              description: 'The active profile database opened through Hermes in query-only mode.'
            }),
            jsx(DefinitionList, {
              rows: [
                ['Status', db.available && db.read_only ? 'Available · read-only' : 'Check configuration'],
                ['Schema', `v${db.schema_version}`],
                ['Database', db.path],
                ['Size', `${formatCount(db.size_bytes)} bytes · WAL ${formatCount(db.wal_size_bytes)} bytes`],
                ['Last modified', formatDate(db.last_modified_at)],
                ['Search', `${db.fts_enabled ? 'FTS enabled' : 'FTS unavailable'} · ${db.trigram_fts_enabled ? 'trigram enabled' : 'trigram unavailable'}`]
              ]
            })
          ]
        }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Store counts', description: 'Raw rows in the active Hermes profile.' }),
            jsx(DefinitionList, {
              rows: [
                ['Sessions', formatCount(data.counts.sessions)],
                ['Messages', formatCount(data.counts.messages)],
                ['Model usage rows', formatCount(data.counts.model_usage_rows)],
                ['Delegations', formatCount(data.counts.delegations)]
              ]
            })
          ]
        }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Privacy posture', description: 'Design constraints enforced by the backend.' }),
            jsx(DefinitionList, {
              rows: [
                ['Network upload', data.privacy.network_upload ? 'Enabled' : 'None'],
                ['Mutation endpoints', String(data.privacy.mutation_endpoints)],
                ['Snippets', data.privacy.snippets_redacted_and_bounded ? 'Redacted and bounded' : 'Review required'],
                ['Connection', data.privacy.database_connection],
                ['Plugin version', data.plugin.version]
              ]
            })
          ]
        })
      ]
    })
  })
}

function SessionLensPage({ ctx }) {
  const viewport = useValue(host.state.viewport)
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(() => ctx.storage.get('activeTab', 'sessions'))
  const [daysText, setDaysText] = useState(() => String(ctx.storage.get('days', 30)))
  const days = Number(daysText) || 0
  const overviewQuery = useQuery({
    queryKey: [PLUGIN_ID, 'overview', days],
    queryFn: () => ctx.rest(apiPath('/overview', { days })),
    refetchInterval: 60_000
  })

  useEffect(() => ctx.storage.set('activeTab', tab), [ctx, tab])
  useEffect(() => ctx.storage.set('days', days), [ctx, days])

  const refresh = () => queryClient.invalidateQueries({ queryKey: [PLUGIN_ID] })
  let content = jsx(SessionsView, { ctx, days, narrow: Boolean(viewport?.narrow) })
  if (tab === 'overview') content = jsx(OverviewView, { query: overviewQuery })
  if (tab === 'operations') content = jsx(OperationsView, { ctx, days })
  if (tab === 'tools') content = jsx(ToolsView, { ctx, days })
  if (tab === 'skills') content = jsx(SkillsViewPanel, { ctx, days })
  if (tab === 'system') content = jsx(SystemView, { ctx })

  return jsxs('div', {
    style: { color: color.primary, display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: 0 },
    children: [
      jsxs('header', {
        style: { alignItems: 'center', display: 'flex', gap: '1rem', justifyContent: 'space-between', padding: '0.75rem 1rem' },
        children: [
          jsxs('div', {
            style: { minWidth: 0 },
            children: [
              jsx('h1', {
                style: { fontSize: '1.0625rem', fontWeight: 680, letterSpacing: '-0.015em', lineHeight: 1.3, margin: 0 },
                children: 'Session Lens'
              }),
              jsx('p', {
                style: { color: color.tertiary, fontSize: '0.6875rem', lineHeight: 1.4, margin: '0.12rem 0 0' },
                children: 'Session evidence, runtime health, and work orchestration—grounded in local Hermes records.'
              })
            ]
          }),
          jsxs('div', {
            style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.5rem', justifyContent: 'flex-end' },
            children: [
              jsx(SegmentedControl, { options: timeOptions, value: daysText, onChange: setDaysText }),
              jsx(Button, {
                variant: 'outline',
                size: 'icon-xs',
                onClick: refresh,
                'aria-label': 'Refresh Session Lens',
                title: 'Refresh Session Lens',
                children: jsx(Codicon, { name: overviewQuery.isFetching ? 'sync~spin' : 'refresh' })
              })
            ]
          })
        ]
      }),
      jsx(StatStrip, { overview: overviewQuery.data }),
      jsx('nav', {
        'aria-label': 'Session Lens views',
        style: { borderBottom: border, display: 'flex', overflowX: 'auto', padding: '0 0.65rem' },
        children: pageTabs.map(item =>
          jsx('button', {
            type: 'button',
            onClick: () => setTab(item.id),
            'aria-current': tab === item.id ? 'page' : undefined,
            style: {
              alignItems: 'center',
              background: 'transparent',
              border: 'none',
              borderBottom: tab === item.id ? `2px solid ${color.accent}` : '2px solid transparent',
              color: tab === item.id ? color.primary : color.tertiary,
              cursor: 'pointer',
              display: 'inline-flex',
              fontSize: '0.75rem',
              fontWeight: tab === item.id ? 650 : 500,
              gap: '0.35rem',
              padding: '0.62rem 0.7rem',
              whiteSpace: 'nowrap'
            },
            children: jsxs(Fragment, { children: [jsx(Codicon, { name: item.codicon, size: '0.75rem' }), item.label] })
          }, item.id)
        )
      }),
      jsx('div', { style: { display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }, children: content })
    ]
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Hermes Session Lens',
  description: 'Native read-only session telemetry, trace, runtime health, profiles, schedules, and Kanban operations.',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(SessionLensPage, { ctx })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { path: ROUTE, label: 'Session Lens', codicon: 'graph-line' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'session-lens.open',
          label: 'Session Lens: Open telemetry',
          keywords: ['sessions', 'tokens', 'cost', 'tools', 'skills', 'failures', 'telemetry', 'profiles', 'gateway', 'schedules', 'kanban'],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}
