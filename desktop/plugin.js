/*
THESIS: One native observatory connects session evidence to Hermes operations without becoming a separate dashboard.
OWN-WORLD: Hermes Desktop theme variables, SDK controls, codicons, compact borders, tabular data, and restrained status pills.
STORY: Find a session, verify its trace and accounting, then inspect runtime, profile, and schedule health without leaving Hermes.
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
  success: 'var(--ui-success)',
  successSoft: 'color-mix(in srgb, var(--ui-success) 12%, transparent)',
  warning: 'var(--ui-warning)',
  warningSoft: 'color-mix(in srgb, var(--ui-warning) 12%, transparent)',
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
  { id: '0', label: 'All' },
  { id: 'custom', label: 'Custom' }
]
const pageTabs = [
  { id: 'sessions', label: 'Sessions', codicon: 'list-tree' },
  { id: 'overview', label: 'Overview', codicon: 'graph' },
  { id: 'operations', label: 'Operations', codicon: 'pulse' },
  { id: 'tools', label: 'Tools', codicon: 'tools' },
  { id: 'system', label: 'System', codicon: 'server-environment' },
  { id: 'ai-usage', label: 'AI Usage', codicon: 'dashboard' },
  { id: 'ai-models', label: 'AI Models', codicon: 'symbol-enum' }
]

// ============================================================================
// SHARED FOUNDATION
// ============================================================================

function apiPath(path, params = {}) {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&')
  return query ? `${path}?${query}` : path
}

function dateInputValue(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function dateDaysAgo(days) {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() - days)
  return dateInputValue(date)
}

function normaliseDateInput(value, fallback) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ''))) return fallback
  const parsed = new Date(`${value}T00:00:00`)
  return Number.isNaN(parsed.getTime()) ? fallback : String(value)
}

function periodParams(range, customStart, customEnd) {
  if (range !== 'custom') return { days: Number(range) || 0 }
  const start = new Date(`${customStart}T00:00:00`)
  const end = new Date(`${customEnd}T00:00:00`)
  end.setDate(end.getDate() + 1)
  return {
    start_at: Math.floor(start.getTime() / 1000),
    end_at: Math.floor(end.getTime() / 1000)
  }
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

function formatRelativeTime(timestamp) {
  const date = timestampDate(timestamp)
  if (!date) return 'last used unknown'
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000))
  if (seconds < 60) return 'last used just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `last used ${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `last used ${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `last used ${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `last used ${months}mo ago`
  return `last used ${Math.floor(months / 12)}y ago`
}

function metricTone(value) {
  if (value === null || value === undefined) return 'neutral'
  const numeric = Number(value)
  if (numeric < 0.02) return 'success'
  if (numeric <= 0.05) return 'warning'
  return 'danger'
}

function toneColor(tone) {
  if (tone === 'success') return color.success
  if (tone === 'warning') return color.warning
  if (tone === 'danger') return color.danger
  return color.tertiary
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

function DateField({ label, value, onChange, min, max }) {
  return jsxs('label', {
    style: { alignItems: 'center', color: color.tertiary, display: 'inline-flex', fontSize: '0.6875rem', gap: '0.35rem' },
    children: [
      jsx('span', { children: label }),
      jsx(Input, {
        type: 'date',
        value,
        min,
        max,
        required: true,
        onChange: event => onChange(event.target.value),
        'aria-label': `${label} date`,
        style: { fontVariantNumeric: 'tabular-nums', width: '8.6rem' }
      })
    ]
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

// ============================================================================
// SESSIONS
// ============================================================================

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

function tableSortValue(column, row) {
  const value = column.sortValue ? column.sortValue(row) : row[column.key]
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'boolean') return value ? 1 : 0
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  return String(value).toLocaleLowerCase()
}

function tableRowKey(row, index, columns) {
  const signature = columns.map(column => {
    const value = row[column.key]
    return value === null || value === undefined || typeof value === 'object' ? '' : String(value)
  }).join('\u001f')
  return `${signature}\u001e${index}`
}

function SimpleTable({ columns, rows, emptyTitle = 'Nothing recorded', emptyDescription }) {
  const [sortState, setSortState] = useState(null)
  const sortedRows = useMemo(() => {
    const materialRows = (rows || []).map((row, index) => ({
      row,
      index,
      key: tableRowKey(row, index, columns)
    }))
    if (!sortState) return materialRows
    const column = columns.find(item => item.key === sortState.key)
    if (!column) return materialRows
    const direction = sortState.direction === 'desc' ? -1 : 1
    return materialRows
      .map(item => ({ ...item, value: tableSortValue(column, item.row) }))
      .sort((left, right) => {
        if (left.value === null && right.value === null) return left.index - right.index
        if (left.value === null) return 1
        if (right.value === null) return -1
        const comparison = typeof left.value === 'number' && typeof right.value === 'number'
          ? left.value - right.value
          : String(left.value).localeCompare(String(right.value), undefined, { numeric: true, sensitivity: 'base' })
        return comparison === 0 ? left.index - right.index : comparison * direction
      })
  }, [columns, rows, sortState])

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
                'aria-sort': sortState?.key === column.key
                  ? (sortState.direction === 'asc' ? 'ascending' : 'descending')
                  : 'none',
                style: {
                  background: color.surface,
                  borderBottom: border,
                  padding: 0,
                  whiteSpace: 'nowrap'
                },
                children: jsx('button', {
                  type: 'button',
                  onClick: () => setSortState(current => ({
                    key: column.key,
                    direction: current?.key === column.key && current.direction === 'asc' ? 'desc' : 'asc'
                  })),
                  'aria-label': `Sort by ${column.label}${sortState?.key === column.key ? `, currently ${sortState.direction === 'asc' ? 'ascending' : 'descending'}` : ''}`,
                  title: `Sort by ${column.label}`,
                  style: {
                    alignItems: 'center',
                    background: 'transparent',
                    border: 'none',
                    color: sortState?.key === column.key ? color.primary : color.tertiary,
                    cursor: 'pointer',
                    display: 'flex',
                    font: 'inherit',
                    fontSize: '0.6875rem',
                    fontWeight: sortState?.key === column.key ? 650 : 600,
                    gap: '0.3rem',
                    justifyContent: column.align === 'right' ? 'flex-end' : 'flex-start',
                    outlineColor: color.accent,
                    padding: '0.5rem 0.65rem',
                    textAlign: column.align || 'left',
                    width: '100%'
                  },
                  children: [
                    jsx('span', { children: column.label }),
                    jsx(Codicon, {
                      name: sortState?.key === column.key
                        ? (sortState.direction === 'asc' ? 'arrow-small-up' : 'arrow-small-down')
                        : 'arrow-swap',
                      size: '0.7rem',
                      style: { color: sortState?.key === column.key ? color.accent : color.quaternary },
                      'aria-hidden': true
                    })
                  ]
                })
              }, column.key)
            )
          })
        }),
        jsx('tbody', {
          children: sortedRows.map((item, rowIndex) => {
            const row = item.row
            return jsx('tr', {
              children: columns.map(column =>
                jsx('td', {
                  style: {
                    ...tabular,
                    borderBottom: rowIndex === sortedRows.length - 1 ? 'none' : border,
                    color: column.muted ? color.tertiary : color.primary,
                    padding: '0.58rem 0.65rem',
                    textAlign: column.align || 'left',
                    verticalAlign: 'top'
                  },
                  children: column.render ? column.render(row) : row[column.key]
                }, column.key)
              )
            }, item.key)
          })
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
              { key: 'task', label: 'Task', muted: true, sortValue: row => row.task || 'main', render: row => row.task || 'main' },
              { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
              { key: 'api_call_count', label: 'Calls', align: 'right', render: row => formatCount(row.api_call_count) },
              { key: 'cost', label: 'Cost', align: 'right', sortValue: row => row.display_cost_usd, render: row => formatCost(row.display_cost_usd, row.cost_kind) }
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
        children: `${formatCount(failures.length)} shown in the bounded event scan; ${formatCount(detectedTotal || failures.length)} confirmed failure${detectedTotal === 1 ? '' : 's'} in the full session. Review the recorded result before drawing conclusions.`
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

function TraceView({ ctx, sessionId, period }) {
  const [limit, setLimit] = useState(100)
  useEffect(() => setLimit(100), [sessionId])
  const traceQuery = useQuery({
    queryKey: [PLUGIN_ID, 'trace', sessionId, limit],
    queryFn: () => ctx.rest(apiPath(`/sessions/${encodeURIComponent(sessionId)}/trace`, { limit })),
    enabled: Boolean(sessionId),
    placeholderData: previous => previous
  })
  const telemetryQuery = useQuery({
    queryKey: [PLUGIN_ID, 'session-telemetry', sessionId, period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/telemetry', { ...period, session_id: sessionId })),
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

function SessionDetail({ query, detailTab, setDetailTab, ctx, period, onBack }) {
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
    { id: 'files', label: `Files ${detail.files?.length || 0}` }
  ]
  let content = jsx(SessionSummary, { detail })
  if (detailTab === 'trace') content = jsx(TraceView, { ctx, sessionId: session.id, period })
  if (detailTab === 'tools') content = jsx(ToolEvents, { events: detail.tools })
  if (detailTab === 'failures') content = jsx(FailureInspector, { failures: detail.failures, detectedTotal: session.failure_count })
  if (detailTab === 'files') content = jsx(FilesView, { files: detail.files, truncated: detail.analysis?.truncated })

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

function AttentionBanner({ data, onSelect, dismissedIds, onDismiss, onRestore }) {
  const dismissed = new Set(dismissedIds || [])
  const allSessions = data?.sessions || []
  const sessions = allSessions.filter(session => !dismissed.has(session.id))
  const dismissedCount = allSessions.length - sessions.length
  if (!allSessions.length) return null
  if (!sessions.length) {
    return jsxs('div', {
      style: { alignItems: 'center', borderBottom: border, color: color.quaternary, display: 'flex', fontSize: '0.625rem', gap: '0.4rem', padding: '0.3rem 0.8rem' },
      children: [
        jsx('span', { children: `${formatCount(dismissedCount)} attention note${dismissedCount === 1 ? '' : 's'} dismissed.` }),
        jsx('button', {
          type: 'button',
          onClick: onRestore,
          style: { background: 'transparent', border: 'none', color: color.accent, cursor: 'pointer', font: 'inherit', fontSize: '0.625rem', outlineColor: color.accent, padding: 0, textDecoration: 'underline', textUnderlineOffset: '2px' },
          children: 'Restore'
        })
      ]
    })
  }
  const openCount = sessions.filter(session => session.ended_at === null || session.ended_at === undefined).length
  const reapedCount = sessions.length - openCount
  const hiddenFlagged = Math.max(0, (Number(data.totals?.flagged) || allSessions.length) - allSessions.length)
  const summaryParts = []
  if (openCount) summaryParts.push(`${formatCount(openCount)} open past ${formatCount(data.thresholds?.open_hours || 24)}h`)
  if (reapedCount) summaryParts.push(`${formatCount(reapedCount)} reaped with heavy spend`)
  return jsxs('div', {
    role: 'status',
    style: { background: color.warningSoft, borderBottom: border, display: 'grid', gap: '0.35rem', padding: '0.55rem 0.8rem' },
    children: [
      jsxs('div', {
        style: { alignItems: 'center', color: color.warning, display: 'flex', fontSize: '0.6875rem', fontWeight: 650, gap: '0.4rem' },
        children: [
          jsx(Codicon, { name: 'warning', size: '0.75rem' }),
          jsx('span', { style: { flex: 1 }, children: `${formatCount(sessions.length)} session${sessions.length === 1 ? '' : 's'} need attention — ${summaryParts.join(' · ')}` }),
          dismissedCount > 0
            ? jsx('button', {
                type: 'button',
                onClick: onRestore,
                style: { background: 'transparent', border: 'none', color: color.warning, cursor: 'pointer', font: 'inherit', fontSize: '0.625rem', fontWeight: 400, outlineColor: color.accent, padding: 0, textDecoration: 'underline', textUnderlineOffset: '2px' },
                children: `restore ${formatCount(dismissedCount)} dismissed`
              })
            : null,
          jsx('button', {
            type: 'button',
            onClick: () => onDismiss(sessions.map(session => session.id)),
            title: 'Dismiss all current attention notes. Restore brings them back.',
            style: { background: 'transparent', border: 'none', color: color.warning, cursor: 'pointer', font: 'inherit', fontSize: '0.625rem', fontWeight: 400, outlineColor: color.accent, padding: 0, textDecoration: 'underline', textUnderlineOffset: '2px' },
            children: 'dismiss all'
          })
        ]
      }),
      jsx('div', {
        style: { display: 'grid', gap: '0.15rem' },
        children: sessions.slice(0, 5).map(session => jsxs('div', {
          style: { alignItems: 'baseline', display: 'flex', gap: '0.45rem' },
          children: [
            jsxs('button', {
              type: 'button',
              onClick: () => onSelect(session.id),
              title: 'Show this session in the list below',
              style: {
                alignItems: 'baseline',
                background: 'transparent',
                border: 'none',
                color: color.secondary,
                cursor: 'pointer',
                display: 'flex',
                flex: 1,
                font: 'inherit',
                fontSize: '0.6875rem',
                gap: '0.6rem',
                justifyContent: 'space-between',
                minWidth: 0,
                outlineColor: color.accent,
                padding: '0.12rem 0',
                textAlign: 'left'
              },
              children: [
                jsxs('span', {
                  style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                  children: [
                    jsx('span', { style: { color: session.severity === 'danger' ? color.danger : color.primary, fontWeight: 650 }, children: session.title || session.id }),
                    jsx('span', { style: { color: color.tertiary }, children: ` — ${session.reason}` })
                  ]
                }),
                jsx('span', {
                  style: { ...tabular, color: color.tertiary, flexShrink: 0 },
                  children: `${formatCount(session.total_tokens)} tok · ${formatCost(session.display_cost_usd, session.cost_kind)}`
                })
              ]
            }),
            jsx('button', {
              type: 'button',
              onClick: () => onDismiss([session.id]),
              'aria-label': `Dismiss the attention note for ${session.title || session.id}`,
              title: 'Dismiss this note',
              style: { alignItems: 'center', background: 'transparent', border: 'none', color: color.quaternary, cursor: 'pointer', display: 'flex', flexShrink: 0, outlineColor: color.accent, padding: '0.05rem' },
              children: jsx(Codicon, { name: 'close', size: '0.7rem' })
            })
          ]
        }, session.id))
      }),
      sessions.length > 5 || hiddenFlagged > 0
        ? jsx('span', { style: { color: color.tertiary, fontSize: '0.625rem' }, children: `${formatCount(Math.max(0, sessions.length - 5) + hiddenFlagged)} more flagged; search or sort by tokens to review the rest.` })
        : null
    ]
  })
}

function SessionsView({ ctx, period, narrow, drill }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('failures')
  const [failuresOnly, setFailuresOnly] = useState(false)
  const [limit, setLimit] = useState(50)
  const [selected, setSelected] = useState(null)
  const [detailTab, setDetailTab] = useState('summary')
  const [narrowPane, setNarrowPane] = useState('list')
  const debouncedSearch = useDebounced(search, 250)

  useEffect(() => setLimit(50), [debouncedSearch, sort, failuresOnly, period.days, period.start_at, period.end_at])
  useEffect(() => {
    if (narrow) setNarrowPane('list')
  }, [narrow])
  useEffect(() => {
    if (!drill) return
    setSearch(drill.search || '')
    setFailuresOnly(Boolean(drill.failuresOnly))
    setSort('failures')
    setNarrowPane('list')
  }, [drill])

  const [dismissedAttention, setDismissedAttention] = useState(() => {
    const stored = ctx.storage.get('attentionDismissed')
    return Array.isArray(stored) ? stored.filter(item => typeof item === 'string').slice(0, 100) : []
  })
  useEffect(() => {
    ctx.storage.set('attentionDismissed', dismissedAttention)
  }, [ctx, dismissedAttention])
  const attentionQuery = useQuery({
    queryKey: [PLUGIN_ID, 'attention', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/attention', period)),
    refetchInterval: 120_000
  })
  const listQuery = useQuery({
    queryKey: [PLUGIN_ID, 'sessions', period.days, period.start_at, period.end_at, debouncedSearch, sort, failuresOnly, limit],
    queryFn: () =>
      ctx.rest(apiPath('/sessions', {
        ...period,
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
      narrow && narrowPane === 'detail'
        ? null
        : jsx(AttentionBanner, {
            data: attentionQuery.data,
            dismissedIds: dismissedAttention,
            onDismiss: ids => setDismissedAttention(current => [...new Set([...current, ...ids])].slice(-100)),
            onRestore: () => setDismissedAttention([]),
            onSelect: id => {
              setSearch(id)
              setFailuresOnly(false)
              setNarrowPane('list')
            }
          }),
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
                       period,
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

// ============================================================================
// OVERVIEW AND OPERATIONS
// ============================================================================

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

function formatDurationShort(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const value = Number(seconds) || 0
  if (value < 90) return `${Math.round(value)}s`
  if (value < 5400) return `${(value / 60).toFixed(value < 600 ? 1 : 0)}m`
  return `${(value / 3600).toFixed(1)}h`
}

function ProjectsSection({ ctx, period }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'projects', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/projects', period)),
    refetchInterval: 120_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 4 })
  if (query.isError) return jsx('p', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Project rollup is temporarily unavailable.' })
  const data = query.data
  const totals = data.totals || {}
  return jsxs('section', {
    children: [
      jsx(SectionHeading, {
        title: 'Where the spend goes',
        description: `Sessions rolled up by git repository, then working directory, then source. ${formatCount(totals.sessions_without_directory)} of ${formatCount(totals.sessions)} sessions record no directory and group by source.`
      }),
      jsx(SimpleTable, {
        columns: [
          {
            key: 'label',
            label: 'Project',
            render: row => jsxs('span', {
              title: row.path || undefined,
              style: { display: 'grid', gap: '0.1rem', minWidth: 0 },
              children: [
                jsx('span', { style: { fontWeight: 600, overflowWrap: 'anywhere' }, children: row.label }),
                jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: row.kind === 'repo' ? 'git repository' : row.kind === 'directory' ? 'working directory' : 'grouped by source' })
              ]
            })
          },
          { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
          { key: 'total_tokens', label: 'Tokens', align: 'right', render: row => formatCount(row.total_tokens) },
          {
            key: 'recorded_cost_usd',
            label: 'Recorded cost',
            align: 'right',
            render: row => jsxs('span', {
              style: { display: 'grid', gap: '0.1rem', justifyItems: 'end' },
              children: [
                jsx('span', { children: formatCost(row.recorded_cost_usd, 'actual') }),
                Number(row.unpriced_sessions) > 0
                  ? jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: `${formatCount(row.unpriced_sessions)} unpriced` })
                  : null
              ]
            })
          },
          {
            key: 'failure_events',
            label: 'Failures',
            align: 'right',
            render: row => Number(row.failure_events) > 0
              ? jsx(Pill, { tone: 'danger', children: formatCount(row.failure_events) })
              : '0'
          },
          {
            key: 'models',
            label: 'Models',
            muted: true,
            sortValue: row => (row.models || []).map(item => item.model).join(', '),
            render: row => (row.models || []).map(item => _modelBasename(item.model)).join(' · ') || '—'
          },
          { key: 'last_activity_at', label: 'Last active', render: row => formatShortDate(row.last_activity_at), muted: true }
        ],
        rows: data.projects,
        emptyTitle: 'No sessions in this period',
        emptyDescription: 'The selected period contains no recorded sessions to roll up.'
      })
    ]
  })
}

function _modelBasename(model) {
  const text = String(model || '')
  const slash = text.lastIndexOf('/')
  return slash >= 0 ? text.slice(slash + 1) : text
}

function OverviewView({ query, ctx, period }) {
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
        jsx(ProjectsSection, { ctx, period }),
        jsxs('section', {
          children: [
            jsx(SectionHeading, { title: 'Models', description: 'Per-model usage rows include auxiliary work such as compression and title generation.' }),
            jsx(SimpleTable, {
              columns: [
                { key: 'model', label: 'Model' },
                { key: 'billing_provider', label: 'Provider', muted: true, sortValue: row => row.billing_provider || 'unknown', render: row => row.billing_provider || 'unknown' },
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

function ToolsView({ ctx, period }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'tools', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/tools', period)),
    refetchInterval: 60_000
  })
  const skillsQuery = useQuery({
    queryKey: [PLUGIN_ID, 'skills', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/skills', period)),
    refetchInterval: 60_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'Tool analytics unavailable' })
  const data = query.data
  const skills = skillsQuery.data
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1.5rem', margin: '0 auto', maxWidth: '78rem' },
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
          : null,
        skills
          ? jsxs('section', {
              children: [
                jsx(SectionHeading, {
                  title: 'Skills actually invoked',
                  description: skills.definition
                }),
                jsx(SimpleTable, {
                  columns: [
                    { key: 'name', label: 'Skill' },
                    { key: 'view_count', label: 'Loads', align: 'right', render: row => formatCount(row.view_count) },
                    { key: 'manage_count', label: 'Management', align: 'right', render: row => formatCount(row.manage_count) },
                    { key: 'sessions', label: 'Sessions', align: 'right', render: row => formatCount(row.sessions) },
                    { key: 'last_used_at', label: 'Last invoked', render: row => formatShortDate(row.last_used_at), muted: true }
                  ],
                  rows: skills.skills,
                  emptyTitle: 'No explicit skill invocations',
                  emptyDescription: 'Available or loaded skills are intentionally not presented as used.'
                })
              ]
            })
          : skillsQuery.isError
            ? jsx('p', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Skill analytics are temporarily unavailable.' })
            : null
      ]
    })
  })
}

function CompressionStrip({ ctx }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'compression'],
    queryFn: () => ctx.rest('/compression'),
    refetchInterval: 300_000
  })
  const data = query.data
  if (!data) return null
  const distressed = data.fallback_sessions + data.ineffective_sessions + data.failed_sessions + data.cooldown_sessions
  if (!distressed) {
    return jsx('p', {
      style: { color: color.quaternary, fontSize: '0.6875rem', margin: 0 },
      children: `Context compression: no distress recorded across ${formatCount(data.sessions)} sessions.`
    })
  }
  return jsxs('section', {
    style: { display: 'grid', gap: '0.5rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Context compression distress',
        description: 'Sessions where Hermes recorded compression falling back, failing, or not reclaiming space.'
      }),
      jsx('div', {
        style: { borderTop: border, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(7rem, 1fr))', overflowX: 'auto' },
        children: [
          jsx(Metric, { label: 'Fallback streaks', value: formatCount(data.fallback_sessions), danger: data.fallback_sessions > 0 }, 'fallback'),
          jsx(Metric, { label: 'Ineffective passes', value: formatCount(data.ineffective_sessions), danger: data.ineffective_sessions > 0 }, 'ineffective'),
          jsx(Metric, { label: 'Compression failures', value: formatCount(data.failed_sessions), danger: data.failed_sessions > 0 }, 'failed'),
          jsx(Metric, { label: 'In cooldown now', value: formatCount(data.cooldown_sessions), danger: data.cooldown_sessions > 0 }, 'cooldown')
        ]
      }),
      jsx('div', {
        style: { display: 'grid', gap: '0.2rem' },
        children: (data.offenders || []).map(item => jsxs('div', {
          style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
          children: [
            jsx('span', { style: { color: color.secondary, fontSize: '0.6875rem', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: item.title }),
            jsx('span', { style: { ...tabular, color: color.tertiary, flexShrink: 0, fontSize: '0.625rem' }, children: `streak ${formatCount(item.fallback_streak)} · ineffective ${formatCount(item.ineffective_count)}${item.in_cooldown ? ' · cooling down' : ''}` })
          ]
        }, item.id))
      })
    ]
  })
}

function RuntimeHealth({ ctx, period }) {
  const telemetryQuery = useQuery({
    queryKey: [PLUGIN_ID, 'telemetry', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/telemetry', period)),
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
              { key: 'platforms', label: 'Platforms', sortValue: row => row.platforms?.length ? row.platforms.map(item => `${item.name}: ${item.state}`).join(' · ') : 'None recorded', render: row => row.platforms?.length ? row.platforms.map(item => `${item.name}: ${item.state}`).join(' · ') : 'None recorded' },
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
      }),
      jsx(CompressionStrip, { ctx })
    ]
  })
}

function ProfilesView({ ctx, period }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'profiles', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/profiles', period)),
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
          { key: 'running', label: 'Running', align: 'right', sortValue: row => row.outcomes?.running || 0, render: row => formatCount(row.outcomes?.running) },
          { key: 'open', label: 'Open', align: 'right', sortValue: row => row.outcomes?.open || 0, render: row => formatCount(row.outcomes?.open) },
          { key: 'failed', label: 'Failed', align: 'right', sortValue: row => row.outcomes?.failed || 0, render: row => row.outcomes?.failed ? jsx(Pill, { tone: 'danger', children: formatCount(row.outcomes.failed) }) : '0' },
          { key: 'last_activity_at', label: 'Last activity', render: row => formatShortDate(row.last_activity_at), muted: true }
        ],
        rows: data.profiles,
        emptyTitle: 'No profile stores found'
      })
    ]
  })
}

const _RUN_TONES = {
  completed: 'success',
  clean: 'success',
  failed: 'danger',
  cancelled: 'warning',
  running: 'accent',
  open: 'accent'
}

function AgentRunStrip({ runs }) {
  const ordered = [...(runs || [])].reverse()
  return jsx('div', {
    style: { display: 'flex', gap: '0.18rem' },
    children: ordered.map(run => {
      const tone = _RUN_TONES[run.outcome] || 'neutral'
      const fill = tone === 'neutral' ? color.stroke : toneColor(tone)
      return jsx('span', {
        title: `${formatShortDate(run.started_at)} · ${run.outcome} · ${formatDurationShort(run.duration_seconds)} · ${formatCount(run.total_tokens)} tok · ${formatCost(run.display_cost_usd, run.cost_kind)}`,
        style: { background: fill, borderRadius: '2px', display: 'block', height: '0.85rem', opacity: tone === 'neutral' ? 0.6 : 0.9, width: '0.55rem' }
      }, run.session_id)
    })
  })
}

function AgentScoreboard({ ctx, period, onSelectJob }) {
  const query = useQuery({
    queryKey: [PLUGIN_ID, 'agent-runs', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/agent-runs', period)),
    refetchInterval: 120_000
  })
  if (query.isLoading) return jsx(LoadingBlock, { rows: 3 })
  if (query.isError) return jsx('p', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Agent run history is temporarily unavailable.' })
  const data = query.data
  const jobs = data?.jobs || []
  if (!jobs.length) return null
  return jsxs('section', {
    style: { display: 'grid', gap: '0.6rem' },
    children: [
      jsx(SectionHeading, {
        title: 'Agent run health',
        description: `${formatCount(data.totals.runs)} cron runs across ${formatCount(data.totals.jobs)} jobs in the selected period · ${formatCount(data.totals.failed_runs)} failed. Latest run is rightmost.`
      }),
      jsx('div', {
        style: { border, borderRadius: '6px', display: 'grid' },
        children: jobs.map(job => {
          const failTone = job.failed_runs > 0 ? color.danger : color.tertiary
          const streakNote = job.current_streak > 1 ? ` · ${formatCount(job.current_streak)}× ${job.last_outcome} streak` : ''
          return jsxs('div', {
            style: { alignItems: 'center', borderBottom: border, display: 'flex', flexWrap: 'wrap', gap: '0.5rem 1rem', padding: '0.55rem 0.75rem' },
            children: [
              jsx('button', {
                type: 'button',
                onClick: () => onSelectJob(job.label),
                title: 'Show this job\u2019s sessions in the Sessions view',
                style: { background: 'transparent', border: 'none', color: color.primary, cursor: 'pointer', flex: '1 1 14rem', font: 'inherit', fontSize: '0.75rem', fontWeight: 650, minWidth: 0, outlineColor: color.accent, overflowWrap: 'anywhere', padding: 0, textAlign: 'left' },
                children: job.label
              }),
              jsx(AgentRunStrip, { runs: job.runs }),
              jsx('span', {
                style: { ...tabular, color: failTone, flexShrink: 0, fontSize: '0.625rem' },
                children: `${formatCount(job.runs_recorded)} runs · ${formatCount(job.failed_runs)} failed${streakNote}`
              }),
              jsx('span', {
                style: { ...tabular, color: color.quaternary, flexShrink: 0, fontSize: '0.625rem' },
                children: `avg ${formatDurationShort(job.avg_duration_seconds)} · avg ${formatCost(job.avg_cost_usd, 'actual')} · last ${formatRelativeTime(job.last_run_at)}`
              })
            ]
          }, job.label)
        })
      })
    ]
  })
}

function SchedulesView({ ctx, period, onSelectJob }) {
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
      jsx(AgentScoreboard, { ctx, period, onSelectJob }),
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
          { key: 'last_status', label: 'Last status', sortValue: row => row.last_status || (row.last_error ? 'Error' : ''), render: row => row.last_error ? jsx(Pill, { tone: 'danger', title: row.last_error, children: row.last_status || 'Error' }) : row.last_status || '—' },
          { key: 'failure_streak', label: 'Failures', align: 'right', render: row => formatCount(row.failure_streak) }
        ],
        rows: data.schedules,
        emptyTitle: 'No scheduled jobs',
        emptyDescription: 'No cron job metadata was found in the default or named profiles.'
      })
    ]
  })
}

function OperationsView({ ctx, period, onDrill }) {
  const [section, setSection] = useState('health')
  const options = [
    { id: 'health', label: 'Health' },
    { id: 'profiles', label: 'Profiles' },
    { id: 'schedules', label: 'Schedules' }
  ]
  let content = jsx(RuntimeHealth, { ctx, period })
  if (section === 'profiles') content = jsx(ProfilesView, { ctx, period })
  if (section === 'schedules') content = jsx(SchedulesView, { ctx, period, onSelectJob: label => onDrill && onDrill({ search: label }) })
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1.25rem', margin: '0 auto', maxWidth: '84rem' },
      children: [
        jsxs('div', {
          style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.75rem', justifyContent: 'space-between' },
          children: [
            jsx(SectionHeading, { title: 'Operations', description: 'Runtime health, profiles, and schedules for the Hermes agent.' }),
            jsx(SegmentedControl, { options, value: section, onChange: setSection })
          ]
        }),
        content
      ]
    })
  })
}

const usageProviderIcons = {
  codex: 'terminal',
  anthropic: 'comment-discussion',
  deepseek: 'search',
  grok: 'sparkle',
  kimi: 'sparkle',
  nous: 'beaker',
  openrouter: 'globe',
  zai: 'pulse'
}

// ============================================================================
// AI USAGE
// ============================================================================

function usageStatus(provider) {
  const status = provider?.status || 'unavailable'
  if (status === 'ok') return { label: 'Connected', tone: 'accent', icon: 'pass' }
  if (status === 'stale') return { label: 'Last known', tone: 'neutral', icon: 'history' }
  if (status === 'not_configured') return { label: 'Not configured', tone: 'neutral', icon: 'circle-slash' }
  if (status === 'expired') {
    const oauth = String(provider?.auth_source || '').toLowerCase().includes('oauth')
    return { label: oauth ? 'Login expired' : 'Key rejected', tone: 'danger', icon: 'key' }
  }
  if (status === 'forbidden') return { label: 'Access denied', tone: 'danger', icon: 'lock' }
  return { label: 'Unavailable', tone: 'danger', icon: 'warning' }
}

function formatUsageAmount(value, unit) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return null
  const amount = Number(value)
  const normalisedUnit = String(unit || '')
  const currency = normalisedUnit.toUpperCase()
  if (['USD', 'CNY'].includes(currency)) {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(amount)
  }
  if (unit === 'credits') return `${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} credits`
  const formatted = amount.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return normalisedUnit ? `${formatted} ${normalisedUnit}` : formatted
}

function UsageWindow({ window }) {
  const rawUsed = window.percentage_used
  const rawRemaining = window.percentage_remaining
  const hasPercent = rawUsed !== null && rawUsed !== undefined && Number.isFinite(Number(rawUsed))
  const used = hasPercent ? Number(rawUsed) : 0
  const hasRemainingPercent = rawRemaining !== null && rawRemaining !== undefined && Number.isFinite(Number(rawRemaining))
  const remainingPercent = hasRemainingPercent ? Number(rawRemaining) : null
  const danger = hasPercent && used >= 90
  const remainingAmount = formatUsageAmount(window.remaining, window.unit)
  const usedAmount = formatUsageAmount(window.used, window.unit)
  const limitAmount = formatUsageAmount(window.limit, window.unit)
  const value = remainingAmount || (remainingPercent !== null ? `${Math.round(remainingPercent)}% remaining` : 'Recorded balance')
  const exhaustAt = window.kind === 'quota' && hasPercent ? quotaExhaustAt(window, used) : null
  const detailParts = []
  if (usedAmount && limitAmount) detailParts.push(`${usedAmount} used of ${limitAmount}`)
  if (window.detail) detailParts.push(window.detail)
  if (window.reset_at) detailParts.push(`Resets ${formatDate(window.reset_at)}`)
  return jsxs('div', {
    style: { borderTop: border, display: 'grid', gap: '0.45rem', padding: '0.72rem 0' },
    children: [
      jsxs('div', {
        style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
        children: [
          jsx('span', { style: { color: color.secondary, fontSize: '0.75rem', fontWeight: 600 }, children: window.label }),
          jsx('span', {
            style: { ...tabular, color: danger ? color.danger : color.primary, fontSize: '0.75rem', fontWeight: 650, textAlign: 'right' },
            children: value
          })
        ]
      }),
      hasPercent
        ? jsx('div', {
            role: 'progressbar',
            'aria-label': `${window.label} usage`,
            'aria-valuemin': 0,
            'aria-valuemax': 100,
            'aria-valuenow': Math.round(used),
            style: { background: color.surfaceRaised, borderRadius: '999px', height: '0.38rem', overflow: 'hidden' },
            children: jsx('div', {
              style: {
                background: danger ? color.danger : color.accent,
                borderRadius: '999px',
                height: '100%',
                width: `${Math.max(0, Math.min(100, used))}%`
              }
            })
          })
        : null,
      exhaustAt
        ? jsx('div', {
            title: 'Linear extrapolation of the current burn rate over the elapsed share of this window.',
            style: { color: used >= 90 ? color.danger : color.warning, fontSize: '0.6875rem', fontWeight: 600 },
            children: `At this pace, empty ~${formatShortDate(exhaustAt)} — before the reset.`
          })
        : null,
      detailParts.length
        ? jsx('div', {
            style: { color: color.quaternary, fontSize: '0.6875rem', lineHeight: 1.45 },
            children: detailParts.join(' · ')
          })
        : null
    ]
  })
}

function UsageProvider({ provider }) {
  const status = usageStatus(provider)
  const messageDanger = ['expired', 'forbidden', 'unavailable'].includes(provider.status)
  return jsxs('section', {
    style: { border, borderRadius: '6px', minWidth: 0, padding: '0.85rem 1rem' },
    children: [
      jsxs('div', {
        style: { alignItems: 'flex-start', display: 'flex', gap: '0.8rem', justifyContent: 'space-between' },
        children: [
          jsxs('div', {
            style: { display: 'flex', gap: '0.6rem', minWidth: 0 },
            children: [
              jsx(Codicon, { name: usageProviderIcons[provider.provider] || 'dashboard', size: '0.9rem', style: { color: color.tertiary, marginTop: '0.18rem' } }),
              jsxs('div', {
                style: { minWidth: 0 },
                children: [
                  jsx('h4', {
                    style: { color: color.primary, fontSize: '0.75rem', fontWeight: 650, lineHeight: 1.35, margin: 0 },
                    children: provider.label
                  }),
                  jsx('div', {
                    style: { color: color.quaternary, fontSize: '0.6875rem', lineHeight: 1.45, marginTop: '0.12rem' },
                    children: [provider.plan, provider.auth_source].filter(Boolean).join(' · ')
                  })
                ]
              })
            ]
          }),
          jsx(Pill, {
            tone: status.tone,
            children: jsxs(Fragment, { children: [jsx(Codicon, { name: status.icon, size: '0.65rem' }), status.label] })
          })
        ]
      }),
      provider.message
        ? jsx('p', {
            role: messageDanger ? 'alert' : undefined,
            style: {
              background: messageDanger ? color.dangerSoft : color.surface,
              borderRadius: '5px',
              color: messageDanger ? color.danger : color.secondary,
              fontSize: '0.6875rem',
              lineHeight: 1.5,
              margin: '0.75rem 0 0',
              padding: '0.5rem 0.6rem'
            },
            children: provider.status === 'stale'
              ? `Latest refresh failed; showing the last successful reading. ${provider.message}`
              : provider.message
          })
        : null,
      provider.windows?.length
        ? jsx('div', { style: { marginTop: '0.65rem' }, children: provider.windows.map(window => jsx(UsageWindow, { window }, `${provider.provider}-${window.id}`)) })
        : jsx('div', {
            style: { borderTop: border, color: color.quaternary, fontSize: '0.75rem', marginTop: '0.75rem', paddingTop: '0.75rem' },
            children: provider.status === 'not_configured'
              ? 'Sign in or configure this provider in Hermes to expose account usage.'
              : 'No quota windows are available right now.'
          }),
      provider.details?.length
        ? jsx('ul', {
            style: { color: color.tertiary, display: 'grid', fontSize: '0.6875rem', gap: '0.25rem', lineHeight: 1.45, margin: '0.65rem 0 0', paddingLeft: '1rem' },
            children: provider.details.map((detail, index) => jsx('li', { children: detail }, `${provider.provider}-detail-${index}`))
          })
        : null,
      provider.fetched_at
        ? jsx('div', {
            style: { color: color.quaternary, fontSize: '0.625rem', marginTop: '0.65rem' },
            children: `${provider.stale ? 'Last successful reading' : 'Provider checked'} ${formatDate(provider.fetched_at)}`
          })
        : null
    ]
  })
}

function UsageProviderGroup({ title, description, providers, narrow, id }) {
  if (!providers.length) return null
  return jsxs('section', {
    'aria-labelledby': id,
    style: { display: 'grid', gap: '0.65rem' },
    children: [
      jsxs('div', {
        children: [
          jsx('h3', {
            id,
            style: { color: color.primary, fontSize: '0.9375rem', fontWeight: 650, lineHeight: 1.35, margin: 0 },
            children: title
          }),
          jsx('p', {
            style: { color: color.tertiary, fontSize: '0.6875rem', lineHeight: 1.5, margin: '0.15rem 0 0' },
            children: description
          })
        ]
      }),
      jsx('div', {
        style: { display: 'grid', gap: '0.85rem', gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'repeat(2, minmax(0, 1fr))' },
        children: providers.map(provider => jsx(UsageProvider, { provider }, provider.provider))
      })
    ]
  })
}

function AIUsageStatStrip({ data }) {
  const summary = data?.summary || {}
  return jsx('div', {
    style: {
      borderBottom: border,
      borderTop: border,
      display: 'grid',
      gridTemplateColumns: 'repeat(4, minmax(8rem, 1fr))',
      overflowX: 'auto'
    },
    children: [
      jsx(Metric, {
        label: 'Connected providers',
        value: data ? `${formatCount(summary.connected)} / ${formatCount(summary.providers)}` : '—',
        detail: data ? `${formatCount(summary.not_configured)} not configured` : null
      }, 'connected'),
      jsx(Metric, {
        label: 'Needs attention',
        value: data ? formatCount(summary.needs_attention) : '—',
        detail: data ? `${formatCount(summary.stale)} last-known readings` : null,
        danger: Number(summary.needs_attention) > 0
      }, 'attention'),
      jsx(Metric, {
        label: 'Next reset',
        value: summary.next_reset_at ? formatShortDate(summary.next_reset_at) : '—',
        detail: summary.next_reset_at ? formatDate(summary.next_reset_at) : 'No timed window reported'
      }, 'reset'),
      jsx(Metric, {
        label: 'Last refresh',
        value: data?.generated_at ? formatShortDate(data.generated_at) : '—',
        detail: data ? `${data.cached ? 'Cached' : 'Live'} · ${Math.round((data.cache_ttl_seconds || 300) / 60)} min cache` : null
      }, 'refresh')
    ]
  })
}

function AIUsageView({ query, narrow, refreshError }) {
  if (query.isLoading) return jsx(LoadingBlock, { rows: 8 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'AI usage is unavailable' })
  const data = query.data
  const providers = data?.providers || []
  const orderedProviders = [
    ...providers.filter(provider => provider.status === 'ok'),
    ...providers.filter(provider => provider.status !== 'ok')
  ]
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1rem', margin: '0 auto', maxWidth: '84rem' },
      children: [
        jsx(SectionHeading, {
          title: 'Provider allowances and balances',
          description: 'Current account-level usage from the credentials already configured in Hermes. These limits are separate from Session Lens token and cost history.'
        }),
        refreshError
          ? jsx('div', {
              role: 'alert',
              style: { background: color.dangerSoft, borderRadius: '5px', color: color.danger, fontSize: '0.75rem', padding: '0.65rem 0.75rem' },
              children: `Manual refresh failed: ${refreshError}`
            })
          : null,
        jsx(UsageProviderGroup, {
          id: 'supported-ai-usage',
          title: 'Supported providers',
          description: 'Provider-supported account limits and balances resolved through Hermes credentials.',
          providers: orderedProviders,
          narrow
        }),
        jsxs('div', {
          style: { alignItems: 'flex-start', borderTop: border, color: color.tertiary, display: 'flex', fontSize: '0.6875rem', gap: '0.5rem', lineHeight: 1.5, paddingTop: '0.75rem' },
          children: [
            jsx(Codicon, { name: 'lock', size: '0.75rem', style: { marginTop: '0.15rem' } }),
            jsx('span', {
              children: 'Credentials remain in the Hermes Python backend. Session Lens returns only normalized usage, status, and reset information; it does not read browser cookies or expose tokens to the desktop UI.'
            })
          ]
        })
      ]
    })
  })
}

// ============================================================================
// SYSTEM
// ============================================================================

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
                ['Provider usage checks', data.privacy.provider_usage_requests ? 'Direct to configured providers' : 'Disabled'],
                ['Credentials in desktop UI', data.privacy.provider_credentials_returned_to_desktop ? 'Review required' : 'Never returned'],
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

// ============================================================================
// APPLICATION SHELL
// ============================================================================

function SessionLensPage({ ctx }) {
  const viewport = useValue(host.state.viewport)
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(() => ctx.storage.get('activeTab', 'sessions'))
  const [drill, setDrill] = useState(null)
  const [aiManualRefreshing, setAiManualRefreshing] = useState(false)
  const [aiRefreshError, setAiRefreshError] = useState('')
  const [daysText, setDaysText] = useState(() => {
    const stored = String(ctx.storage.get('timeRange', ctx.storage.get('days', 30)))
    return timeOptions.some(option => option.id === stored) ? stored : '30'
  })
  const [customStart, setCustomStart] = useState(() => normaliseDateInput(ctx.storage.get('customStart'), dateDaysAgo(29)))
  const [customEnd, setCustomEnd] = useState(() => normaliseDateInput(ctx.storage.get('customEnd'), dateDaysAgo(0)))
  const period = useMemo(() => periodParams(daysText, customStart, customEnd), [daysText, customStart, customEnd])
  const overviewQuery = useQuery({
    queryKey: [PLUGIN_ID, 'overview', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/overview', period)),
    refetchInterval: 60_000
  })
  const aiUsageQuery = useQuery({
    queryKey: [PLUGIN_ID, 'ai-usage'],
    queryFn: () => ctx.rest('/ai-usage'),
    enabled: tab === 'ai-usage' || tab === 'ai-models',
    refetchInterval: tab === 'ai-usage' || tab === 'ai-models' ? 300_000 : false
  })
  const aiModelsQuery = useQuery({
    queryKey: [PLUGIN_ID, 'ai-models', period.days, period.start_at, period.end_at],
    queryFn: () => ctx.rest(apiPath('/ai-models', period)),
    enabled: tab === 'ai-models',
    refetchInterval: tab === 'ai-models' ? 300_000 : false
  })

  useEffect(() => {
    ctx.storage.set('activeTab', tab)
  }, [ctx, tab])
  useEffect(() => {
    ctx.storage.set('timeRange', daysText)
    if (daysText !== 'custom') ctx.storage.set('days', Number(daysText) || 0)
  }, [ctx, daysText])
  useEffect(() => {
    ctx.storage.set('customStart', customStart)
  }, [ctx, customStart])
  useEffect(() => {
    ctx.storage.set('customEnd', customEnd)
  }, [ctx, customEnd])

  const refresh = async () => {
    if (tab !== 'ai-usage' && tab !== 'ai-models') {
      queryClient.invalidateQueries({ queryKey: [PLUGIN_ID] })
      return
    }
    setAiManualRefreshing(true)
    setAiRefreshError('')
    const refreshErrors = []
    if (tab === 'ai-models') {
      try {
        const data = await ctx.rest(apiPath('/ai-models', { ...period, fresh: true }))
        queryClient.setQueryData(
          [PLUGIN_ID, 'ai-models', period.days, period.start_at, period.end_at],
          data
        )
      } catch (error) {
        refreshErrors.push(`Model analytics: ${error?.message || String(error || 'refresh failed')}`)
      }
    }
    try {
      const data = await ctx.rest('/ai-usage?fresh=true')
      queryClient.setQueryData([PLUGIN_ID, 'ai-usage'], data)
    } catch (error) {
      refreshErrors.push(`OAuth quotas: ${error?.message || String(error || 'the backend did not return data')}`)
    } finally {
      if (refreshErrors.length) setAiRefreshError(refreshErrors.join(' · '))
      setAiManualRefreshing(false)
    }
  }
  const updateCustomStart = value => {
    if (!value) return
    setCustomStart(value)
    if (value > customEnd) setCustomEnd(value)
  }
  const updateCustomEnd = value => {
    if (!value) return
    setCustomEnd(value)
    if (value < customStart) setCustomStart(value)
  }
  const drillToSessions = filters => {
    setDrill({ ...filters, key: Date.now() })
    setTab('sessions')
  }
  let content = jsx(SessionsView, { ctx, period, narrow: Boolean(viewport?.narrow), drill })
  if (tab === 'overview') content = jsx(OverviewView, { query: overviewQuery, ctx, period })
  if (tab === 'operations') content = jsx(OperationsView, { ctx, period, onDrill: drillToSessions })
  if (tab === 'tools') content = jsx(ToolsView, { ctx, period })
  if (tab === 'system') content = jsx(SystemView, { ctx })
  if (tab === 'ai-usage') content = jsx(AIUsageView, {
    query: aiUsageQuery,
    narrow: Boolean(viewport?.narrow),
    refreshError: aiRefreshError
  })
  if (tab === 'ai-models') content = jsx(AIModelsView, {
    onDrill: drillToSessions,
    query: aiModelsQuery,
    quotaQuery: aiUsageQuery,
    narrow: Boolean(viewport?.narrow),
    refreshError: aiRefreshError
  })

  return jsxs('div', {
    style: { color: color.primary, display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: 0 },
    children: [
      jsxs('header', {
        style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.75rem 1rem', justifyContent: 'space-between', padding: '0.75rem 1rem' },
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
                children: 'Session evidence, account usage, runtime health, and work orchestration—grounded in Hermes records.'
              })
            ]
          }),
          jsxs('div', {
            style: { alignItems: 'center', display: 'flex', flex: '1 1 auto', flexWrap: 'wrap', gap: '0.5rem', justifyContent: 'flex-end' },
            children: [
              tab === 'ai-usage'
                ? jsx(Pill, {
                    tone: 'accent',
                    children: jsxs(Fragment, { children: [jsx(Codicon, { name: 'pulse', size: '0.65rem' }), 'Live account quotas'] })
                  })
                : jsx(SegmentedControl, { options: timeOptions, value: daysText, onChange: setDaysText }),
              tab !== 'ai-usage' && daysText === 'custom'
                ? jsxs('div', {
                    'aria-label': 'Custom date range',
                    style: { alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: '0.45rem' },
                    children: [
                      jsx(DateField, { label: 'Start', value: customStart, onChange: updateCustomStart }),
                      jsx(DateField, { label: 'End', value: customEnd, onChange: updateCustomEnd })
                    ]
                  })
                : null,
              jsx(Button, {
                variant: 'outline',
                size: 'icon-xs',
                onClick: refresh,
                'aria-label': 'Refresh Session Lens',
                title: 'Refresh Session Lens',
                disabled: aiManualRefreshing,
                children: jsx(Codicon, {
                  name: (tab === 'ai-usage' || tab === 'ai-models'
                    ? aiManualRefreshing || aiUsageQuery.isFetching || (tab === 'ai-models' && aiModelsQuery.isFetching)
                    : overviewQuery.isFetching) ? 'sync~spin' : 'refresh'
                })
              })
            ]
          })
        ]
      }),
      tab === 'ai-usage'
        ? jsx(AIUsageStatStrip, { data: aiUsageQuery.data })
        : tab === 'ai-models'
          ? jsx(AIModelsStatStrip, { data: aiModelsQuery.data })
          : jsx(StatStrip, { overview: overviewQuery.data }),
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

// ============================================================================
// AI MODELS
// ============================================================================

function formatModelCost(model) {
  if (model.cost_kind === 'subscription') return 'sub.'
  if (model.cost_kind === 'mixed') return model.cost_usd > 0 ? `${formatCost(model.cost_usd, 'actual')} + sub.` : 'sub.'
  if (model.cost_kind === 'free') return '$0.00'
  if (model.cost_kind === 'actual' || model.cost_kind === 'estimated') return formatCost(model.cost_usd, model.cost_kind)
  if (Number(model.sessions) === 0 && (model.routes || []).some(route => route.subscription)) return 'sub.'
  return '—'
}

function distinctValues(values) {
  return [...new Set((values || []).filter(value => value !== null && value !== undefined && String(value).trim() !== ''))]
}

function quotaWindowDurationSeconds(label) {
  const text = String(label || '').toLowerCase()
  if (text.includes('week')) return 7 * 86400
  if (text.includes('month')) return 30 * 86400
  if (text.includes('day')) return 86400
  const hourMatch = text.match(/(\d+(?:\.\d+)?)\s*[- ]?hour|\((\d+(?:\.\d+)?)h\)/)
  if (hourMatch) return Number(hourMatch[1] || hourMatch[2]) * 3600
  return null
}

function quotaExhaustAt(window, burnPercent) {
  const duration = quotaWindowDurationSeconds(window?.label)
  const reset = timestampDate(window?.reset_at)
  const burn = Number(burnPercent)
  if (!duration || !reset || !Number.isFinite(burn) || burn <= 0) return null
  const startedAtMs = reset.getTime() - duration * 1000
  const elapsedMs = Date.now() - startedAtMs
  if (elapsedMs <= 0) return null
  const elapsedPercent = (elapsedMs / (duration * 1000)) * 100
  if (elapsedPercent < 10) return null
  if (burn <= elapsedPercent) return null
  const exhaustMs = startedAtMs + elapsedMs * (100 / burn)
  if (exhaustMs >= reset.getTime()) return null
  return exhaustMs / 1000
}

function modelQuota(model, quotaData, allModels) {
  const routes = model.routes || []
  const oauthInventoryRoutes = routes.filter(route => route.oauth && route.subscription)
  if (!oauthInventoryRoutes.length) return { kind: 'pay_go' }
  const periodRoutes = routes.filter(route => Number(route.requests) > 0)
  if (!periodRoutes.length) {
    return { kind: 'subscription', available: false, inactive: true, route: oauthInventoryRoutes[0] }
  }
  const oauthRoutes = periodRoutes.filter(route => route.oauth && route.subscription)
  if (!oauthRoutes.length) return { kind: 'pay_go' }
  const route = oauthRoutes[0]
  const provider = (quotaData?.providers || []).find(item => item.provider === route.quota_provider)
  const windows = (provider?.windows || []).filter(window =>
    window.kind === 'quota' && window.percentage_used !== null && window.percentage_used !== undefined
  )
  const window = windows.find(item => /week/i.test(item.label)) || windows[0]
  if (!window || !['ok', 'stale'].includes(provider?.status)) {
    return { kind: 'subscription', available: false, provider, route }
  }
  const burn = Math.max(0, Math.min(100, Number(window.percentage_used) || 0))
  const duration = quotaWindowDurationSeconds(window.label)
  const reset = timestampDate(window.reset_at)
  let elapsed = null
  if (duration && reset) {
    const startedAt = reset.getTime() - duration * 1000
    elapsed = Math.max(0, Math.min(100, ((Date.now() - startedAt) / (duration * 1000)) * 100))
  }
  const earlyPeriod = elapsed !== null && elapsed < 10
  let tone = 'neutral'
  if (elapsed !== null && !earlyPeriod) tone = burn <= elapsed ? 'success' : burn - elapsed <= 10 ? 'warning' : 'danger'
  const providerRequests = (allModels || []).reduce((sum, candidate) =>
    sum + (candidate.routes || [])
      .filter(item => item.quota_provider === route.quota_provider && item.oauth && item.subscription)
      .reduce((routeSum, item) => routeSum + (Number(item.requests) || 0), 0), 0)
  const modelRequests = oauthRoutes
    .filter(item => item.quota_provider === route.quota_provider)
    .reduce((sum, item) => sum + (Number(item.requests) || 0), 0)
  const accepted = Number(model.accepted_tasks) || 0
  const capPerAcceptedTask = providerRequests > 0 && accepted >= 10
    ? burn * (modelRequests / providerRequests) / accepted
    : null
  return {
    kind: 'subscription',
    available: true,
    burn,
    elapsed,
    earlyPeriod,
    tone,
    window,
    provider,
    route,
    capPerAcceptedTask,
    exhaustAt: quotaExhaustAt(window, burn)
  }
}

function formatLogWindow(coverage) {
  if (!coverage?.log_start_at) return null
  return `${formatShortDate(coverage.log_start_at)} – ${formatShortDate(coverage.log_end_at)}`
}

function RateValue({ value, numerator, label, sampleCount = 0, sampleThreshold = 20, sampleNoun = 'samples', unavailableReason }) {
  if (unavailableReason) {
    return jsx('span', {
      title: unavailableReason,
      style: { ...tabular, color: color.tertiary, fontWeight: 400 },
      children: '–'
    })
  }
  const samples = Math.max(0, Number(sampleCount) || 0)
  const available = value !== null && value !== undefined && samples > 0
  if (!available) {
    return jsx('span', {
      title: `${label} is unavailable; ${formatCount(samples)} ${sampleNoun}`,
      style: { ...tabular, color: color.tertiary, fontWeight: 400 },
      children: '–'
    })
  }
  const lowSample = samples < sampleThreshold
  if (lowSample) {
    const count = numerator === null || numerator === undefined
      ? Math.round(Number(value) * samples)
      : Math.max(0, Number(numerator) || 0)
    return jsxs('span', {
      title: `${label}: ${formatCount(count)} of ${formatCount(samples)} ${sampleNoun}; below the ${formatCount(sampleThreshold)}-sample floor, so no percentage is shown`,
      style: { display: 'grid', gap: '0.08rem', justifyItems: 'end' },
      children: [
        jsx('span', { style: { ...tabular, color: color.tertiary, fontWeight: 500 }, children: `${formatCount(count)}/${formatCount(samples)}` }),
        jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: sampleNoun })
      ]
    })
  }
  return jsxs('span', {
    title: `${label}: ${(Number(value) * 100).toFixed(1)}% across ${formatCount(samples)} ${sampleNoun}`,
    style: { display: 'grid', gap: '0.08rem', justifyItems: 'end' },
    children: [
      jsx('span', { style: { ...tabular, color: toneColor(metricTone(value)), fontWeight: 650 }, children: `${(Number(value) * 100).toFixed(1)}%` }),
      jsx('span', { style: { ...tabular, color: color.quaternary, fontSize: '0.625rem', whiteSpace: 'nowrap' }, children: `of ${formatCount(samples)} ${sampleNoun}` })
    ]
  })
}

function QuotaBurn({ quota }) {
  if (quota.kind === 'pay_go') {
    return jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'pay-go' })
  }
  if (!quota.available) {
    return jsx('span', {
      title: quota.inactive
        ? 'This OAuth subscription model has no recorded use in the selected period.'
        : 'This is an OAuth subscription route, but no account quota window is available.',
      style: { color: color.tertiary, fontSize: '0.6875rem' },
      children: quota.inactive ? 'sub. · no use' : 'sub. · —'
    })
  }
  const paceLabel = quota.earlyPeriod ? 'early in period' : quota.tone === 'success' ? 'on pace' : quota.tone === 'warning' ? 'watch' : quota.tone === 'danger' ? 'over pace' : ''
  const label = quota.elapsed === null
    ? `${Math.round(quota.burn)}% used`
    : `${Math.round(quota.burn)}% / ${Math.round(quota.elapsed)}% elapsed${paceLabel ? ` · ${paceLabel}` : ''}`
  const forecast = quota.exhaustAt ? `at this pace, empty ~${formatShortDate(quota.exhaustAt)}` : null
  return jsxs('div', {
    title: `${quota.window.label}: ${label}.${forecast ? ` At the current burn rate this window runs out around ${formatDate(quota.exhaustAt)}, before it resets.` : ''} The tick marks billing-period elapsed time; this quota is shared at provider-account level.`,
    style: { display: 'grid', gap: '0.28rem', minWidth: '8.5rem' },
    children: [
      jsx('span', { style: { ...tabular, color: toneColor(quota.tone), fontSize: '0.6875rem', fontWeight: 650 }, children: label }),
      forecast
        ? jsx('span', { style: { ...tabular, color: toneColor(quota.tone), fontSize: '0.625rem' }, children: forecast })
        : null,
      jsxs('div', {
        role: 'progressbar',
        'aria-label': `${quota.window.label} quota burn`,
        'aria-valuemin': 0,
        'aria-valuemax': 100,
        'aria-valuenow': Math.round(quota.burn),
        style: { background: color.surfaceRaised, borderRadius: '999px', height: '0.36rem', overflow: 'hidden', position: 'relative' },
        children: [
          jsx('div', {
            style: {
              background: toneColor(quota.tone),
              borderRadius: '999px',
              height: '100%',
              width: `${quota.burn}%`
            }
          }),
          quota.elapsed !== null
            ? jsx('span', {
                'aria-hidden': true,
                style: {
                  background: color.primary,
                  height: '100%',
                  left: `calc(${quota.elapsed}% - 0.5px)`,
                  opacity: 0.8,
                  position: 'absolute',
                  top: 0,
                  width: '1px'
                }
              })
            : null
        ]
      })
    ]
  })
}

function TrendBars({ rows }) {
  const values = (rows || []).map(row => Number(row.requests) || 0)
  const max = Math.max(...values, 1)
  const description = (rows || []).map(row => `${row.day}: ${formatCount(row.requests)}`).join(', ')
  return jsx('div', {
    role: 'img',
    'aria-label': `Seven-day request trend. ${description || 'No requests recorded.'}`,
    title: description || 'No requests recorded in the seven-day trend window.',
    style: { alignItems: 'end', display: 'flex', gap: '0.16rem', height: '1.45rem', justifyContent: 'flex-end', minWidth: '3.5rem' },
    children: (rows || []).map(row => {
      const value = Number(row.requests) || 0
      const height = value ? Math.max(3, (value / max) * 22) : 2
      return jsx('span', {
        style: {
          background: value ? color.accent : color.stroke,
          borderRadius: '1px 1px 0 0',
          display: 'block',
          height: `${height}px`,
          opacity: value ? 0.82 : 0.7,
          width: '0.28rem'
        }
      }, row.day)
    })
  })
}

function modelVerdict(model, sampleThreshold) {
  const failures = model.failures || {}
  const reliability = model.work_reliability || {}
  const threshold = Math.max(1, Number(sampleThreshold) || 20)
  const parts = []
  const failureSamples = Number(failures.samples) || 0
  const failureRate = failures.rate
  if (Number(model.requests) === 0) {
    parts.push('No recorded calls in this period.')
  } else if (!failureSamples) {
    parts.push('API health unmeasured — no bounded-log coverage.')
  } else if (failureSamples < threshold) {
    parts.push(`API log sample thin — ${formatCount(failures.observed_failures)}/${formatCount(failureSamples)} logged calls failed.`)
  } else if (failureRate !== null && failureRate !== undefined && failureRate > 0.05) {
    parts.push(`API unstable — ${(failureRate * 100).toFixed(1)}% of ${formatCount(failureSamples)} logged calls failed.`)
  } else {
    parts.push('API steady.')
  }
  const eligible = Number(reliability.eligible_tasks) || 0
  const gate = Math.max(1, Number(reliability.sample_threshold) || threshold)
  const bound = reliability.failure_rate_upper_bound_95
  const boundText = bound === null || bound === undefined ? null : formatPercent(bound)
  if (Number(reliability.rank) > 0 && Number(reliability.ranked_models) > 0) {
    parts.push(`Ranked #${formatCount(reliability.rank)} of ${formatCount(reliability.ranked_models)} — work-failure risk ≤ ${boundText || '—'}.`)
  } else if (eligible > 0) {
    parts.push(`Too little finished work to rank — ${formatCount(eligible)} of ${formatCount(gate)} tasks${boundText ? `; true failure could reach ${boundText}` : ''}.`)
  } else {
    parts.push('No scored work evidence yet.')
  }
  return parts.join(' ')
}

function WorkEvidenceCell({ model }) {
  const reliability = model.work_reliability || {}
  const eligible = Number(reliability.eligible_tasks) || 0
  const gate = Math.max(1, Number(reliability.sample_threshold) || 20)
  const bound = reliability.failure_rate_upper_bound_95
  const ranked = Number(reliability.rank) > 0 && Number(reliability.ranked_models) > 0
  const progress = Math.max(0, Math.min(100, (eligible / gate) * 100))
  const headline = ranked ? `#${formatCount(reliability.rank)} of ${formatCount(reliability.ranked_models)}` : `${formatCount(eligible)} / ${formatCount(gate)} tasks`
  const riskText = bound === null || bound === undefined ? 'no scored work' : `risk ≤ ${formatPercent(bound)}`
  return jsxs('div', {
    title: ranked
      ? `Reliability rank by lowest 95% Wilson upper failure bound across ${formatCount(reliability.ranked_models)} comparable models; ${formatCount(eligible)} eligible main-role tasks.`
      : `${formatCount(eligible)} eligible main-role tasks of the ${formatCount(gate)} required before ranking; the 95% Wilson upper bound caps how bad the true failure rate could be.`,
    style: { display: 'grid', gap: '0.28rem', minWidth: '7.5rem' },
    children: [
      jsx('span', { style: { ...tabular, color: ranked ? color.primary : color.tertiary, fontSize: '0.6875rem', fontWeight: ranked ? 650 : 500 }, children: headline }),
      jsx('div', {
        role: 'progressbar',
        'aria-label': 'Eligible tasks toward the ranking gate',
        'aria-valuemin': 0,
        'aria-valuemax': 100,
        'aria-valuenow': Math.round(progress),
        style: { background: color.surfaceRaised, borderRadius: '999px', height: '0.32rem', overflow: 'hidden' },
        children: jsx('div', {
          style: { background: ranked ? color.success : color.accent, borderRadius: '999px', height: '100%', opacity: ranked ? 0.9 : 0.7, width: `${progress}%` }
        })
      }),
      jsx('span', { style: { ...tabular, color: color.quaternary, fontSize: '0.625rem' }, children: riskText })
    ]
  })
}

function LayerPane({ title, meta, children }) {
  return jsxs('section', {
    style: { border, borderRadius: '6px', display: 'grid', gap: '0.65rem', gridTemplateRows: 'auto 1fr', minWidth: 0, padding: '0.75rem 0.85rem' },
    children: [
      jsxs('div', {
        style: { alignItems: 'baseline', display: 'flex', flexWrap: 'wrap', gap: '0.3rem 0.75rem', justifyContent: 'space-between' },
        children: [
          jsx('h3', { style: { color: color.primary, fontSize: '0.8125rem', fontWeight: 650, margin: 0 }, children: title }),
          jsx('span', { style: { ...tabular, color: color.quaternary, fontSize: '0.625rem' }, children: meta })
        ]
      }),
      jsx('div', { style: { display: 'grid', gap: '0.7rem', minWidth: 0 }, children })
    ]
  })
}

function EvidenceLink({ onClick, title, children, style }) {
  return jsx('button', {
    type: 'button',
    onClick,
    title,
    style: {
      background: 'transparent',
      border: 'none',
      color: color.accent,
      cursor: 'pointer',
      font: 'inherit',
      outlineColor: color.accent,
      padding: 0,
      textAlign: 'right',
      textDecoration: 'underline',
      textUnderlineOffset: '2px',
      ...style
    },
    children
  })
}

function ApiLayerPane({ model, coverage, onDrill }) {
  const failures = model.failures || {}
  const logSamples = Number(failures.samples) || 0
  const window = formatLogWindow(coverage)
  const mixRows = [...(model.task_types || []), ...(model.auxiliary_tasks || []).map(row => ({ ...row, auxiliary: true }))]
  const totalMixCalls = mixRows.reduce((sum, row) => sum + (Number(row.requests) || 0), 0)
  const toolCalls = Number(failures.tool_calls) || 0
  const toolFailures = Number(failures.tool_failures) || 0
  const latency = model.latency || {}
  return jsxs(LayerPane, {
    title: 'API layer',
    meta: `${formatCount(model.requests)} calls in period · logs ${window || 'unavailable'}`,
    children: [
      jsxs('div', {
        style: { display: 'grid', gap: '0.45rem' },
        children: [
          jsx('div', { style: { color: color.quaternary, fontSize: '0.625rem', fontWeight: 600 }, children: 'Request mix by session type (calls)' }),
          mixRows.length
            ? mixRows.map(row => {
                const calls = Number(row.requests) || 0
                const share = totalMixCalls ? (calls / totalMixCalls) * 100 : 0
                return jsxs('div', {
                  style: { display: 'grid', gap: '0.2rem' },
                  children: [
                    jsxs('div', {
                      style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
                      children: [
                        jsx('span', { style: { color: color.secondary, fontSize: '0.6875rem', fontWeight: 600 }, children: row.auxiliary ? `${row.task_type} (auxiliary)` : row.task_type }),
                        jsx('span', { style: { ...tabular, color: color.tertiary, fontSize: '0.625rem' }, children: `${formatCount(calls)} calls · ${share.toFixed(0)}%` })
                      ]
                    }),
                    jsx('div', {
                      style: { background: color.surfaceRaised, borderRadius: '999px', height: '0.28rem', overflow: 'hidden' },
                      children: jsx('div', { style: { background: color.accent, borderRadius: '999px', height: '100%', opacity: 0.75, width: `${share}%` } })
                    })
                  ]
                }, `${row.task_type}-${row.auxiliary ? 'aux' : 'main'}`)
              })
            : jsx('span', { style: { color: color.quaternary, fontSize: '0.6875rem' }, children: 'No recorded calls in this period.' })
        ]
      }),
      jsx('div', {
        style: { borderTop: border, display: 'grid', gridTemplateColumns: 'repeat(3, minmax(5.5rem, 1fr))' },
        children: [
          ['Rate limits', failures.rate_limits],
          ['Timeouts', failures.timeouts],
          ['API errors', failures.errors]
        ].map(([label, value]) => jsxs('div', {
          style: { borderBottom: border, padding: '0.5rem 0.45rem' },
          children: [
            jsx('div', { style: { color: color.quaternary, fontSize: '0.625rem' }, children: label }),
            jsxs('div', {
              style: { alignItems: 'baseline', display: 'flex', flexWrap: 'wrap', gap: '0.15rem 0.35rem', marginTop: '0.12rem' },
              children: [
                jsx('span', { style: { ...tabular, color: Number(value) > 0 ? color.danger : color.primary, fontSize: '0.8125rem', fontWeight: 650 }, children: formatCount(value) }),
                jsx('span', { style: { ...tabular, color: color.quaternary, fontSize: '0.625rem' }, children: `of ${formatCount(logSamples)} logged` })
              ]
            })
          ]
        }, label))
      }),
      jsxs('div', {
        style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
        children: [
          jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Tool failures (session records)' }),
          (() => {
            const label = toolCalls > 0
              ? `${formatCount(toolFailures)} of ${formatCount(toolCalls)} tool calls · ${((toolFailures / toolCalls) * 100).toFixed(1)}%`
              : formatCount(toolFailures)
            const valueStyle = { ...tabular, color: toolFailures > 0 ? color.danger : color.primary, fontSize: '0.75rem', fontWeight: 650 }
            return onDrill && toolFailures > 0
              ? jsx(EvidenceLink, {
                  onClick: () => onDrill({ search: model.model_id, failuresOnly: true }),
                  title: 'Open the Sessions view filtered to this model with failures only',
                  style: valueStyle,
                  children: label
                })
              : jsx('span', { style: valueStyle, children: label })
          })()
        ]
      }),
      jsxs('div', {
        style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
        children: [
          jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: 'Total latency' }),
          jsx('span', { style: { ...tabular, color: color.primary, fontSize: '0.75rem', fontWeight: 650 }, children: `p50 ${formatSeconds(latency.total_p50_seconds)} · p95 ${formatSeconds(latency.total_p95_seconds)} · ${formatCount(latency.samples)} samples` })
        ]
      })
    ]
  })
}

function WorkLedgerRow({ row, acceptance }) {
  const eligible = Number(row.eligible_tasks) || 0
  const completed = Number(row.completed_tasks) || 0
  const clean = Number(row.clean_completions) || 0
  const recovered = Number(row.recovered_tasks) || 0
  const excluded = Number(row.excluded_tasks) || 0
  const unknown = Number(row.unknown_tasks) || 0
  const switched = Number(row.switched_away_tasks) || 0
  const note = row.label === 'Orchestration'
    ? 'not scored by design'
    : unknown > 0
      ? `${formatCount(unknown)} without attributable evidence`
      : switched > 0
        ? `${formatCount(switched)} switched away`
        : excluded > 0
          ? `${formatCount(excluded)} not eligible`
          : 'no eligible tasks'
  const title = acceptance && Number(acceptance.eligible_sessions) > 0
    ? `${acceptance.acceptance_basis}; ${formatCount(acceptance.accepted_sessions)}/${formatCount(acceptance.eligible_sessions)} accepted first attempt`
    : acceptance?.acceptance_basis
  const numberCell = (value, key) => jsx('td', {
    style: { ...tabular, borderBottom: border, color: eligible > 0 ? color.primary : color.tertiary, fontSize: '0.6875rem', padding: '0.34rem 0.3rem', textAlign: 'right' },
    children: value
  }, key)
  return jsxs('tr', {
    title,
    children: [
      jsx('td', { style: { borderBottom: border, color: color.secondary, fontSize: '0.6875rem', fontWeight: 600, padding: '0.34rem 0.3rem 0.34rem 0' }, children: row.label }, 'task'),
      eligible > 0
        ? [
            numberCell(formatCount(eligible), 'eligible'),
            numberCell(`${formatCount(completed)}/${formatCount(eligible)}`, 'completed'),
            numberCell(`${formatCount(clean)}/${formatCount(eligible)}`, 'clean'),
            numberCell(`${formatCount(recovered)}/${formatCount(eligible)}`, 'recovered')
          ]
        : jsx('td', {
            colSpan: 4,
            style: { borderBottom: border, color: color.quaternary, fontSize: '0.625rem', fontStyle: 'italic', padding: '0.34rem 0.3rem', textAlign: 'right' },
            children: note
          }, 'note')
    ]
  }, row.label)
}

function WorkLedgerPane({ model, quota, onDrill }) {
  const reliability = model.work_reliability || {}
  const eligible = Number(reliability.eligible_tasks) || 0
  const bound = reliability.failure_rate_upper_bound_95
  const ranked = Number(reliability.rank) > 0 && Number(reliability.ranked_models) > 0
  const unrecovered = Number(reliability.unrecovered_failures) || 0
  const acceptanceByType = new Map((model.task_types || []).map(row => [row.task_type, row]))
  const hasEnoughAcceptedTasks = Number(model.accepted_tasks) >= 10
  const costPerAccepted = !hasEnoughAcceptedTasks
    ? 'insufficient data'
    : quota.kind === 'subscription'
      ? (quota.capPerAcceptedTask === null || quota.capPerAcceptedTask === undefined ? '—' : `~${quota.capPerAcceptedTask.toFixed(2)}% of cap`)
      : ['actual', 'estimated', 'free', 'mixed'].includes(model.cost_kind)
        ? formatCost((Number(model.cost_usd) || 0) / model.accepted_tasks, 'actual')
        : '—'
  const headerCell = label => jsx('th', {
    scope: 'col',
    style: { borderBottom: border, color: color.quaternary, fontSize: '0.625rem', fontWeight: 600, padding: '0 0.3rem 0.3rem', textAlign: label === 'Task' ? 'left' : 'right', whiteSpace: 'nowrap' },
    children: label
  })
  return jsxs(LayerPane, {
    title: 'Work ledger',
    meta: onDrill && eligible > 0
      ? jsx(EvidenceLink, {
          onClick: () => onDrill({ search: model.model_id }),
          title: "Open the Sessions view filtered to this model's sessions",
          style: { ...tabular, fontSize: '0.625rem' },
          children: `${formatCount(eligible)} eligible tasks`
        })
      : `${formatCount(eligible)} eligible tasks`,
    children: [
      ranked
        ? jsxs('div', {
            style: { alignItems: 'baseline', display: 'flex', flexWrap: 'wrap', gap: '0.3rem 0.75rem', justifyContent: 'space-between' },
            children: [
              jsx('span', { style: { color: color.primary, fontSize: '0.6875rem', fontWeight: 650 }, children: `Reliability rank #${formatCount(reliability.rank)} of ${formatCount(reliability.ranked_models)} comparable models` }),
              jsx('span', {
                title: 'Models rank by the lowest 95% Wilson upper bound on the unrecovered-failure rate.',
                style: { ...tabular, color: toneColor(metricTone(bound)), fontSize: '0.6875rem', fontWeight: 650 },
                children: `true failure ≤ ${formatPercent(bound)}`
              })
            ]
          })
        : null,
      eligible > 0
        ? jsx('span', {
            style: { ...tabular, color: color.tertiary, fontSize: '0.625rem' },
            children: `${formatCount(unrecovered)}/${formatCount(eligible)} unrecovered · ${formatCount(reliability.recovered_tasks)}/${formatCount(eligible)} recovered after an API failure`
          })
        : null,
      jsx('div', {
        style: { overflowX: 'auto' },
        children: jsxs('table', {
          style: { borderCollapse: 'collapse', minWidth: '17rem', width: '100%' },
          children: [
            jsx('thead', {
              children: jsx('tr', { children: ['Task', 'Eligible', 'Completed', 'Clean', 'Recovered'].map(headerCell) })
            }),
            jsx('tbody', {
              children: (reliability.by_task_type || []).map(row => jsx(WorkLedgerRow, { row, acceptance: acceptanceByType.get(row.label) }, row.label))
            })
          ]
        })
      }),
      model.auxiliary_tasks?.length
        ? jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', fontStyle: 'italic' }, children: 'Auxiliary jobs are not scored by design.' })
        : null,
      reliability.by_route?.length > 1
        ? jsxs('div', {
            style: { display: 'grid', gap: '0.3rem' },
            children: [
              jsx('div', { style: { color: color.quaternary, fontSize: '0.625rem', fontWeight: 600 }, children: 'By route' }),
              ...reliability.by_route.map(row => {
                const routeEligible = Number(row.eligible_tasks) || 0
                return jsxs('div', {
                  style: { alignItems: 'baseline', display: 'flex', gap: '0.75rem', justifyContent: 'space-between' },
                  children: [
                    jsx('span', { style: { color: color.secondary, fontSize: '0.6875rem', minWidth: 0, overflowWrap: 'anywhere' }, children: row.label }),
                    jsx('span', {
                      style: { ...tabular, color: color.tertiary, flexShrink: 0, fontSize: '0.625rem' },
                      children: routeEligible > 0
                        ? `${formatCount(row.completed_tasks)}/${formatCount(routeEligible)} completed · ${formatCount(row.unrecovered_failures)} unrecovered`
                        : 'no eligible tasks'
                    })
                  ]
                }, row.label)
              })
            ]
          })
        : null,
      jsxs('div', {
        style: { alignItems: 'baseline', borderTop: border, display: 'flex', gap: '0.75rem', justifyContent: 'space-between', paddingTop: '0.55rem' },
        children: [
          jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: quota.kind === 'subscription' ? 'Cap per accepted task' : 'Cost per accepted task' }),
          jsx('span', { style: { ...tabular, color: color.primary, fontSize: '0.75rem', fontWeight: 650 }, children: costPerAccepted })
        ]
      }),
      hasEnoughAcceptedTasks && quota.kind === 'subscription' && quota.capPerAcceptedTask !== null && quota.capPerAcceptedTask !== undefined
        ? jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', lineHeight: 1.45 }, children: 'Estimate allocated by this model’s share of recorded OAuth requests in the selected period; provider quota is account-level.' })
        : null
    ]
  })
}

function ModelExpanded({ model, quota, coverage, narrow, onDrill }) {
  const reliability = model.work_reliability || {}
  const eligible = Number(reliability.eligible_tasks) || 0
  const gate = Math.max(1, Number(reliability.sample_threshold) || Number(coverage?.rate_sample_threshold) || 20)
  const bound = reliability.failure_rate_upper_bound_95
  const rankEligible = Boolean(reliability.rank_eligible)
  const quotaInsight = quota.available && !quota.earlyPeriod && quota.elapsed !== null && quota.burn > quota.elapsed
    ? `${quota.window.label} is burning ${Math.round(quota.burn - quota.elapsed)} percentage points faster than the billing period is elapsing.`
    : null
  const insight = quotaInsight || model.insight
  const routeLabels = distinctValues((model.routes || []).map(route => route.label))
  const routeMappingNote = model.route_mapping_source === 'unmapped'
    ? `add a model-id glob under ${coverage?.route_mapping_config_path || 'plugins.entries.session-lens.settings.model_route_mappings'}`
    : model.route_mapping_source === 'historical'
      ? `mapping inferred from recorded routes using ${model.route_mapping_pattern}`
      : model.route_mapping_source === 'config'
        ? `mapping matched config pattern ${model.route_mapping_pattern}`
        : null
  const window = formatLogWindow(coverage)
  const banner = !rankEligible
    ? eligible > 0
      ? {
          tone: 'warning',
          text: `Not rankable yet — ${formatCount(eligible)} of ${formatCount(gate)} eligible tasks. True failure rate could be anywhere up to ${bound === null || bound === undefined ? '100%' : formatPercent(bound)}.`
        }
      : {
          tone: 'neutral',
          text: 'No scored work evidence in this period — the API layer below is the only signal.'
        }
    : null
  const provenance = distinctValues([
    `Routes: ${routeLabels.join(' · ') || 'Unknown'}${routeMappingNote ? ` (${routeMappingNote})` : ''}.`,
    `Log window: ${window || 'unavailable'}. Fail rate counts API errors, timeouts, and rate limits from bounded local Hermes logs; time-to-first-token is not recorded, so latency is total response time.`,
    `Work ledger: scores completed main-role tasks and terminal model/API failures. Open, cancelled, orchestration, auxiliary, ambiguous, and uncovered runs are excluded; missing evidence is never treated as success. Ranking needs ${formatCount(gate)} eligible tasks and orders by the lowest 95% Wilson upper failure bound.`,
    'Classification assigns one primary type per session in this order: Orchestration, Coding, Writing, Analysis, General. Acceptance: General/Analysis use the eligible-closed-session proxy; Coding requires a resolved code save or commit; Writing a resolved non-code artifact write. Retry/switch counts rewinds, near-identical resends to the same model within five minutes, and same-role model changes.'
  ])
  return jsxs('div', {
    style: {
      background: 'transparent',
      borderTop: border,
      boxSizing: 'border-box',
      display: 'grid',
      gap: '0.85rem',
      padding: '0.9rem 1rem',
      position: 'static',
      width: '100%'
    },
    children: [
      banner
        ? jsxs('div', {
            role: banner.tone === 'warning' ? 'status' : undefined,
            style: {
              alignItems: 'flex-start',
              background: banner.tone === 'warning' ? color.warningSoft : color.surfaceRaised,
              borderRadius: '5px',
              color: banner.tone === 'warning' ? color.warning : color.secondary,
              display: 'flex',
              fontSize: '0.6875rem',
              fontWeight: banner.tone === 'warning' ? 600 : 400,
              gap: '0.45rem',
              lineHeight: 1.5,
              padding: '0.5rem 0.6rem'
            },
            children: [jsx(Codicon, { name: banner.tone === 'warning' ? 'warning' : 'info', size: '0.72rem', style: { marginTop: '0.15rem' } }), jsx('span', { children: banner.text })]
          })
        : null,
      jsxs('div', {
        style: { display: 'grid', gap: '0.85rem', gridTemplateColumns: narrow ? '1fr' : 'minmax(19rem, 1fr) minmax(19rem, 1fr)' },
        children: [
          jsx(ApiLayerPane, { model, coverage, onDrill }),
          jsx(WorkLedgerPane, { model, quota, onDrill })
        ]
      }),
      insight
        ? jsxs('div', {
            style: {
              alignItems: 'flex-start',
              background: quotaInsight ? (quota.tone === 'danger' ? color.dangerSoft : color.warningSoft) : color.surfaceRaised,
              borderRadius: '5px',
              color: quotaInsight ? toneColor(quota.tone) : color.secondary,
              display: 'flex',
              fontSize: '0.6875rem',
              gap: '0.45rem',
              lineHeight: 1.5,
              padding: '0.5rem 0.6rem'
            },
            children: [jsx(Codicon, { name: 'lightbulb', size: '0.72rem', style: { marginTop: '0.15rem' } }), jsx('span', { children: insight })]
          })
        : null,
      jsxs('div', {
        style: { borderTop: border, display: 'grid', gap: '0.25rem', paddingTop: '0.6rem' },
        children: [
          jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', fontWeight: 600 }, children: 'Provenance' }),
          ...provenance.map(note => jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', lineHeight: 1.45 }, children: note }, note))
        ]
      })
    ]
  })
}

function routingSummary(models, threshold) {
  return ['Coding', 'Writing', 'Analysis', 'General'].map(taskType => {
    const candidates = []
    for (const model of models || []) {
      const row = (model.work_reliability?.by_task_type || []).find(item => item.label === taskType)
      if (!row || !Number(row.eligible_tasks)) continue
      candidates.push({ model, row })
    }
    candidates.sort((left, right) => {
      const leftGated = Number(left.row.eligible_tasks) >= threshold ? 0 : 1
      const rightGated = Number(right.row.eligible_tasks) >= threshold ? 0 : 1
      if (leftGated !== rightGated) return leftGated - rightGated
      const leftBound = left.row.failure_rate_upper_bound_95 ?? 1
      const rightBound = right.row.failure_rate_upper_bound_95 ?? 1
      if (leftBound !== rightBound) return leftBound - rightBound
      return Number(right.row.eligible_tasks) - Number(left.row.eligible_tasks)
    })
    return { taskType, best: candidates[0] || null }
  })
}

function RoutingSummary({ models, coverage, onDrill }) {
  const threshold = Math.max(1, Number(coverage?.rate_sample_threshold) || 20)
  const rows = routingSummary(models, threshold)
  if (!rows.some(row => row.best)) return null
  const anyGatePassed = rows.some(row => row.best && Number(row.best.row.eligible_tasks) >= threshold)
  return jsxs('section', {
    style: { border, borderRadius: '6px', display: 'grid', gap: '0.6rem', padding: '0.75rem 0.85rem' },
    children: [
      jsxs('div', {
        style: { alignItems: 'baseline', display: 'flex', flexWrap: 'wrap', gap: '0.3rem 0.75rem', justifyContent: 'space-between' },
        children: [
          jsx('h3', { style: { color: color.primary, fontSize: '0.8125rem', fontWeight: 650, margin: 0 }, children: 'Best current evidence by task type' }),
          jsx('span', {
            style: { color: color.quaternary, fontSize: '0.625rem' },
            children: anyGatePassed
              ? 'Ranked by lowest 95% Wilson upper failure bound per task type.'
              : `Provisional — no model has ${formatCount(threshold)} eligible tasks in any single type yet.`
          })
        ]
      }),
      jsx('div', {
        style: { display: 'grid', gap: '0.6rem', gridTemplateColumns: 'repeat(auto-fit, minmax(11rem, 1fr))' },
        children: rows.map(({ taskType, best }) => {
          if (!best) {
            return jsxs('div', {
              style: { display: 'grid', gap: '0.15rem' },
              children: [
                jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', fontWeight: 600 }, children: taskType }),
                jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem', fontStyle: 'italic' }, children: 'no scored evidence yet' })
              ]
            }, taskType)
          }
          const eligible = Number(best.row.eligible_tasks) || 0
          const completed = Number(best.row.completed_tasks) || 0
          const bound = best.row.failure_rate_upper_bound_95
          const gated = eligible >= threshold
          const evidence = `${formatCount(completed)}/${formatCount(eligible)} completed${bound !== null && bound !== undefined ? ` · risk ≤ ${formatPercent(bound)}` : ''}`
          const name = best.model.display_name
          return jsxs('div', {
            style: { display: 'grid', gap: '0.15rem', minWidth: 0 },
            children: [
              jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', fontWeight: 600 }, children: taskType }),
              onDrill
                ? jsx(EvidenceLink, {
                    onClick: () => onDrill({ search: best.model.model_id }),
                    title: `Open the Sessions view filtered to ${name}`,
                    style: { fontSize: '0.75rem', fontWeight: 650, textAlign: 'left' },
                    children: name
                  })
                : jsx('span', { style: { color: color.primary, fontSize: '0.75rem', fontWeight: 650 }, children: name }),
              jsx('span', { style: { ...tabular, color: gated ? color.secondary : color.tertiary, fontSize: '0.625rem' }, children: evidence }),
              gated
                ? null
                : jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', fontStyle: 'italic' }, children: `below the ${formatCount(threshold)}-task floor` })
            ]
          }, taskType)
        })
      })
    ]
  })
}

function AIModelsTable({ models, quotaData, coverage, narrow, onDrill }) {
  const [sortState, setSortState] = useState({ key: 'total_tokens', direction: 'desc' })
  const [expanded, setExpanded] = useState(() => new Set())
  const rateSampleThreshold = Math.max(1, Number(coverage?.rate_sample_threshold) || 20)
  const rows = useMemo(() => (models || []).map((model, index) => ({
    model,
    index,
    quota: modelQuota(model, quotaData, models)
  })), [models, quotaData])
  const columns = [
    { key: 'model', label: 'Model', value: item => item.model.display_name },
    { key: 'route', label: 'Route', value: item => item.model.route_label || '' },
    { key: 'requests', label: 'Requests', align: 'right', value: item => item.model.requests },
    { key: 'total_tokens', label: 'Tokens in / out / cached', align: 'right', value: item => item.model.total_tokens },
    { key: 'cost', label: 'Cost · quota (weekly)', value: item => ['actual', 'estimated', 'free', 'mixed'].includes(item.model.cost_kind) ? item.model.cost_usd : null },
    { key: 'failure', label: 'Fail rate', align: 'right', value: item => Number(item.model.requests) > 0 ? item.model.failures?.rate : null, sample: item => item.model.failures?.samples },
    { key: 'retry', label: 'Retry / switch', align: 'right', value: item => item.model.retry_switch_rate, sample: item => item.model.retry_switch_samples },
    { key: 'work', label: 'Work evidence', value: item => item.model.work_reliability?.failure_rate_upper_bound_95 ?? null, sample: item => item.model.work_reliability?.eligible_tasks },
    { key: 'latency', label: 'Total latency', align: 'right', value: item => Number(item.model.requests) > 0 ? item.model.latency?.total_p50_seconds : null },
    { key: 'trend', label: 'Trend', align: 'right', value: item => (item.model.trend || []).reduce((sum, row) => sum + (Number(row.requests) || 0), 0) }
  ]
  const sortedRows = useMemo(() => {
    const column = columns.find(item => item.key === sortState.key) || columns[3]
    const direction = sortState.direction === 'desc' ? -1 : 1
    return [...rows].sort((left, right) => {
      const leftValue = column.value(left)
      const rightValue = column.value(right)
      if (column.sample) {
        const leftAdequate = leftValue !== null && leftValue !== undefined && Number(column.sample(left) || 0) >= rateSampleThreshold
        const rightAdequate = rightValue !== null && rightValue !== undefined && Number(column.sample(right) || 0) >= rateSampleThreshold
        if (leftAdequate !== rightAdequate) return leftAdequate ? -1 : 1
        if (!leftAdequate) return left.index - right.index
      }
      if (leftValue === null || leftValue === undefined) return rightValue === null || rightValue === undefined ? left.index - right.index : 1
      if (rightValue === null || rightValue === undefined) return -1
      const comparison = typeof leftValue === 'number' && typeof rightValue === 'number'
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue), undefined, { numeric: true, sensitivity: 'base' })
      return comparison === 0 ? left.index - right.index : comparison * direction
    })
  }, [rows, sortState, rateSampleThreshold])
  const toggle = modelId => setExpanded(current => {
    const next = new Set(current)
    if (next.has(modelId)) next.delete(modelId)
    else next.add(modelId)
    return next
  })

  if (!models?.length) {
    return jsx(EmptyState, { title: 'No recorded models yet', description: 'Models appear automatically as soon as Hermes records a session with a model ID.' })
  }
  return jsx('div', {
    style: { border, borderRadius: '6px', overflowX: 'auto' },
    children: jsx('table', {
      style: { borderCollapse: 'collapse', fontSize: '0.75rem', minWidth: '94rem', width: '100%' },
      children: [
        jsx('thead', {
          children: jsx('tr', {
            children: columns.map(column => jsx('th', {
              scope: 'col',
              'aria-sort': sortState.key === column.key ? (sortState.direction === 'asc' ? 'ascending' : 'descending') : 'none',
              style: { background: color.surface, borderBottom: border, padding: 0, whiteSpace: 'nowrap' },
              children: jsx('button', {
                type: 'button',
                onClick: () => setSortState(current => ({
                  key: column.key,
                  direction: current.key === column.key && current.direction === 'desc' ? 'asc' : 'desc'
                })),
                'aria-label': `Sort by ${column.label}`,
                title: `Sort by ${column.label}`,
                style: {
                  alignItems: 'center',
                  background: 'transparent',
                  border: 'none',
                  color: sortState.key === column.key ? color.primary : color.tertiary,
                  cursor: 'pointer',
                  display: 'flex',
                  font: 'inherit',
                  fontSize: '0.6875rem',
                  fontWeight: sortState.key === column.key ? 650 : 600,
                  gap: '0.3rem',
                  justifyContent: column.align === 'right' ? 'flex-end' : 'flex-start',
                  outlineColor: color.accent,
                  padding: '0.5rem 0.65rem',
                  width: '100%'
                },
                children: [
                  jsx('span', { children: column.label }),
                  jsx(Codicon, { name: sortState.key === column.key ? (sortState.direction === 'asc' ? 'arrow-small-up' : 'arrow-small-down') : 'arrow-swap', size: '0.7rem', style: { color: sortState.key === column.key ? color.accent : color.quaternary } })
                ]
              })
            }, column.key))
          })
        }),
        jsx('tbody', {
          children: sortedRows.flatMap(item => {
            const model = item.model
            const isExpanded = expanded.has(model.model_id)
            const detailId = `model-detail-${item.index}`
            const verdict = modelVerdict(model, rateSampleThreshold)
            const cells = [
              jsx('td', {
                style: { borderBottom: isExpanded ? 'none' : border, minWidth: '17rem', padding: 0, verticalAlign: 'top' },
                children: jsx('button', {
                  type: 'button',
                  onClick: event => {
                    event.stopPropagation()
                    toggle(model.model_id)
                  },
                  'aria-expanded': isExpanded,
                  'aria-controls': detailId,
                  style: { alignItems: 'flex-start', background: 'transparent', border: 'none', color: color.primary, cursor: 'pointer', display: 'flex', gap: '0.48rem', outlineColor: color.accent, padding: '0.62rem 0.65rem', textAlign: 'left', width: '100%' },
                  children: [
                    jsx(Codicon, { name: isExpanded ? 'chevron-down' : 'chevron-right', size: '0.72rem', style: { color: color.quaternary, marginTop: '0.16rem' } }),
                    jsxs('span', {
                      style: { display: 'grid', gap: '0.12rem', minWidth: 0 },
                      children: [
                        jsx('span', { style: { fontWeight: 650, overflowWrap: 'anywhere' }, children: model.display_name }),
                        jsx('span', { style: { color: color.tertiary, fontSize: '0.6875rem' }, children: `${model.provider_label} · ${formatRelativeTime(model.last_used_at)}` }),
                        jsx('span', { style: { color: color.quaternary, fontSize: '0.625rem', lineHeight: 1.4, maxWidth: '22rem', whiteSpace: 'normal' }, children: verdict })
                      ]
                    })
                  ]
                })
              }, 'model'),
              jsx('td', { title: model.route_mapping_source === 'unmapped' ? `Add a model-id glob under ${coverage?.route_mapping_config_path || 'plugins.entries.session-lens.settings.model_route_mappings'}.` : model.route_mapping_source === 'historical' ? `Inferred from recorded routes using ${model.route_mapping_pattern}.` : model.route_mapping_source === 'config' ? `Mapped by config pattern ${model.route_mapping_pattern}.` : undefined, style: { borderBottom: isExpanded ? 'none' : border, color: color.secondary, minWidth: '9rem', padding: '0.62rem 0.65rem', verticalAlign: 'top' }, children: jsxs('span', { children: [model.route_label || 'Unmapped (edit in config)', model.route_count > 1 ? jsx('span', { style: { color: color.quaternary, display: 'block', fontSize: '0.625rem', marginTop: '0.12rem' }, children: `+${model.route_count - 1} more` }) : null] }) }, 'route'),
              jsx('td', { style: { ...tabular, borderBottom: isExpanded ? 'none' : border, padding: '0.62rem 0.65rem', textAlign: 'right', verticalAlign: 'top' }, children: formatCount(model.requests) }, 'requests'),
              jsx('td', { title: model.cache_coverage === 'partial' ? 'Cached-token coverage is partial across routes.' : model.cache_coverage === 'unavailable' ? 'This route has not demonstrated cached-token reporting in the selected period.' : undefined, style: { ...tabular, borderBottom: isExpanded ? 'none' : border, minWidth: '11rem', padding: '0.62rem 0.65rem', textAlign: 'right', verticalAlign: 'top' }, children: `${formatCount(model.input_tokens)} / ${formatCount(model.output_tokens)} / ${model.cache_tokens === null || model.cache_tokens === undefined ? '–' : formatCount(model.cache_tokens)}` }, 'tokens'),
              jsx('td', {
                style: { borderBottom: isExpanded ? 'none' : border, minWidth: '9.5rem', padding: '0.58rem 0.65rem', verticalAlign: 'top' },
                children: jsxs('div', {
                  style: { display: 'grid', gap: '0.3rem' },
                  children: [
                    jsx('span', { style: { ...tabular, fontWeight: 600 }, children: formatModelCost(model) }),
                    jsx(QuotaBurn, { quota: item.quota })
                  ]
                })
              }, 'cost'),
              jsx('td', { style: { borderBottom: isExpanded ? 'none' : border, padding: '0.62rem 0.65rem', textAlign: 'right', verticalAlign: 'top' }, children: jsx(RateValue, { value: model.failures?.rate, numerator: model.failures?.observed_failures, sampleCount: model.failures?.samples, sampleThreshold: rateSampleThreshold, sampleNoun: 'logged calls', unavailableReason: Number(model.requests) === 0 ? 'activity outside selected period; see the provenance note in the expanded card' : undefined, label: 'API attempt failure rate: errors, timeouts, or rate-limit responses' }) }, 'failure'),
              jsx('td', { style: { borderBottom: isExpanded ? 'none' : border, padding: '0.62rem 0.65rem', textAlign: 'right', verticalAlign: 'top' }, children: jsx(RateValue, { value: model.retry_switch_rate, numerator: model.retry_switch_sessions, sampleCount: model.retry_switch_samples, sampleThreshold: rateSampleThreshold, sampleNoun: 'sessions', label: 'Rewind, same-model prompt resend, or same-role model-switch session rate' }) }, 'retry'),
              jsx('td', { style: { borderBottom: isExpanded ? 'none' : border, padding: '0.58rem 0.65rem', verticalAlign: 'top' }, children: jsx(WorkEvidenceCell, { model }) }, 'work'),
              jsx('td', { title: Number(model.requests) === 0 ? 'activity outside selected period; see the provenance note in the expanded card' : `p95 ${formatSeconds(model.latency?.total_p95_seconds)} · ${model.latency?.samples || 0} bounded-log samples. Hermes does not record time-to-first-token.`, style: { ...tabular, borderBottom: isExpanded ? 'none' : border, minWidth: '6rem', padding: '0.62rem 0.65rem', textAlign: 'right', verticalAlign: 'top' }, children: Number(model.requests) === 0 ? '–' : formatSeconds(model.latency?.total_p50_seconds) }, 'latency'),
              jsx('td', { style: { borderBottom: isExpanded ? 'none' : border, padding: '0.54rem 0.65rem', verticalAlign: 'top' }, children: jsx(TrendBars, { rows: model.trend }) }, 'trend')
            ]
            const detail = isExpanded
              ? jsx('tr', {
                  children: jsx('td', {
                    id: detailId,
                    colSpan: columns.length,
                    style: { borderBottom: border, padding: 0 },
                    children: jsx(ModelExpanded, { model, quota: item.quota, coverage, narrow, onDrill })
                  })
                }, `${model.model_id}-detail`)
              : null
            return [jsx('tr', {
              onClick: () => toggle(model.model_id),
              title: `${isExpanded ? 'Collapse' : 'Expand'} ${model.display_name}`,
              style: { cursor: 'pointer' },
              children: cells
            }, model.model_id), detail].filter(Boolean)
          })
        })
      ]
    })
  })
}

function AIModelsStatStrip({ data }) {
  const summary = data?.summary || {}
  return jsx('div', {
    style: { borderBottom: border, borderTop: border, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(8rem, 1fr))', overflowX: 'auto' },
    children: [
      jsx(Metric, { label: 'Model inventory', value: data ? formatCount(summary.inventory_models ?? summary.models) : '—', detail: data ? `${formatCount(summary.active_models)} active in this period` : null }, 'models'),
      jsx(Metric, { label: 'Requests', value: data ? formatCount(summary.requests) : '—', detail: data ? 'Successful calls recorded by Hermes' : null }, 'requests'),
      jsx(Metric, { label: 'Tokens', value: data ? formatCount(summary.total_tokens) : '—', detail: data ? 'Input + output + recorded cache, including auxiliary jobs' : null }, 'tokens'),
      jsx(Metric, { label: 'Known API cost', value: data ? formatCost(summary.cost_usd, 'actual') : '—', detail: data ? `${formatCount(summary.subscription_models)} subscription models` : null }, 'cost')
    ]
  })
}

function AIModelsView({ query, quotaQuery, narrow, refreshError, onDrill }) {
  if (query.isLoading) return jsx(LoadingBlock, { rows: 9 })
  if (query.isError) return jsx(ErrorBlock, { error: query.error, onRetry: query.refetch, title: 'AI model analytics are unavailable' })
  const data = query.data
  const window = formatLogWindow(data.coverage)
  const toolCalls = Number(data.coverage?.recorded_tool_calls) || 0
  return jsx('div', {
    style: { flex: 1, minHeight: 0, overflow: 'auto', padding: '1rem' },
    children: jsxs('div', {
      style: { display: 'grid', gap: '1rem', margin: '0 auto', maxWidth: '100rem' },
      children: [
        jsx(SectionHeading, {
          title: 'Model performance and efficiency',
          description: 'Each row states a verdict from two separated evidence layers: the API layer (logged calls) and the work ledger (eligible finished tasks). Click a row for the full evidence card.',
          action: window
            ? jsx('span', {
                title: 'Fail rate and latency come from bounded local Hermes agent logs covering this window; samples can exceed the period’s recorded requests when the log window is wider.',
                style: { ...tabular, background: color.surfaceRaised, border, borderRadius: '999px', color: color.tertiary, flexShrink: 0, fontSize: '0.625rem', padding: '0.22rem 0.6rem', whiteSpace: 'nowrap' },
                children: `logs ${window}`
              })
            : null
        }),
        refreshError
          ? jsx('div', { role: 'alert', style: { background: color.dangerSoft, borderRadius: '5px', color: color.danger, fontSize: '0.6875rem', padding: '0.5rem 0.6rem' }, children: `Manual refresh failed: ${refreshError}` })
          : null,
        quotaQuery?.isError
          ? jsx('div', { role: 'status', style: { background: color.warningSoft, borderRadius: '5px', color: color.warning, fontSize: '0.6875rem', padding: '0.5rem 0.6rem' }, children: 'OAuth quota burn is temporarily unavailable; recorded model analytics remain visible.' })
          : null,
        jsx(RoutingSummary, { models: data.models, coverage: data.coverage, onDrill }),
        jsx(AIModelsTable, { models: data.models, quotaData: quotaQuery?.data, coverage: data.coverage, narrow, onDrill }),
        jsxs('div', {
          style: { alignItems: 'flex-start', borderTop: border, color: color.tertiary, display: 'flex', fontSize: '0.6875rem', gap: '0.5rem', lineHeight: 1.5, paddingTop: '0.75rem' },
          children: [
            jsx(Codicon, { name: 'info', size: '0.75rem', style: { marginTop: '0.15rem' } }),
            jsx('span', {
              children: `Requests, tokens, routes, and cost come from Hermes session accounting; API-layer rates come from bounded local logs${window ? ` (${window})` : ''}; the work ledger scores eligible finished main-role tasks and ranks by the 95% Wilson upper failure bound after an n=${formatCount(data.coverage?.rate_sample_threshold || 20)} floor. This period recorded ${formatCount(data.coverage?.recorded_failure_events)} failure events, including ${formatCount(data.coverage?.recorded_tool_failures)}${toolCalls ? ` of ${formatCount(toolCalls)}` : ''} tool-call failures (${formatCount(data.coverage?.attributed_tool_failures)} attributed to a model). Full metric definitions live in each card's provenance block.`
            })
          ]
        })
      ]
    })
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Hermes Session Lens',
  description: 'Native read-only session telemetry, account usage, trace, runtime health, profiles, and schedules.',
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
          keywords: ['sessions', 'models', 'tokens', 'cost', 'usage', 'quota', 'codex', 'grok', 'nous', 'openrouter', 'tools', 'skills', 'failures', 'latency', 'telemetry', 'profiles', 'gateway', 'schedules'],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}
