// The workflow as a diagram: every status the master can route a case to, the routes between them
// (labelled with their rules), the path this case has taken, and where it is now.
import { useMemo } from 'react'
import {
  Background, BaseEdge, Handle, MarkerType, Position, ReactFlow,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { CaseStatus, TimelineEvent, Workflow } from '../api'
import { ACTOR_LABEL, RULES } from '../format'
import { edgeId, LAYOUT, STEP, transitions } from '../flow'

type StatusNodeData = {
  label: string; actor: string; visits: number; current: boolean; working: boolean
}
type StatusNode = Node<StatusNodeData, 'status'>

function StatusNodeView({ data }: NodeProps<StatusNode>) {
  const cls = ['flow-node', `actor-${data.actor}`, data.current ? 'current' : '', data.visits ? 'visited' : 'unvisited',
    data.current && data.working ? 'working' : ''].join(' ')
  return (
    <div className={cls}>
      {/* left/right for the main row, top/bottom pairs so routes in each direction don't overlap */}
      <Handle type="target" position={Position.Left} id="l" />
      <Handle type="source" position={Position.Right} id="r" />
      <Handle type="source" position={Position.Top} id="ts" style={{ left: '35%' }} />
      <Handle type="target" position={Position.Top} id="tt" style={{ left: '65%' }} />
      <Handle type="source" position={Position.Bottom} id="bs" style={{ left: '35%' }} />
      <Handle type="target" position={Position.Bottom} id="bt" style={{ left: '65%' }} />
      <div className="flow-node-label">{data.label}</div>
      <div className="flow-node-meta">
        {ACTOR_LABEL[data.actor]}
        {data.visits > 1 && <span> · ×{data.visits}</span>}
      </div>
      {data.current && <div className="flow-node-now">{data.working ? 'working' : 'now'}</div>}
    </div>
  )
}

// an invisible node above the top row, so fitView leaves room for the arcs (it only measures nodes)
const Spacer = () => <div style={{ width: 1, height: 1 }} />
const nodeTypes = { status: StatusNodeView, spacer: Spacer }

type ArcEdgeType = Edge<{ lift: number }, 'arc'>

/** A route over the top row (skipping a box, or going back): an arc whose height is set per edge. */
function ArcEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, label, data }: EdgeProps<ArcEdgeType>) {
  const h = data?.lift ?? 50
  const path = `M ${sourceX},${sourceY} C ${sourceX},${sourceY - h} ${targetX},${targetY - h} ${targetX},${targetY}`
  return (
    <BaseEdge id={id} path={path} markerEnd={markerEnd} label={label} labelX={(sourceX + targetX) / 2}
              labelY={sourceY - h * 0.75} labelShowBg labelBgPadding={[3, 1]} labelBgBorderRadius={3} />
  )
}

const edgeTypes = { arc: ArcEdge }

/** How an edge is drawn, from where its two ends sit on the grid: neighbours on a row get a straight
 *  line; routes that skip a box (or go backwards) arc over the top row; routes between rows are curves,
 *  each on its own path, with separate handles per direction. */
function shape(source: CaseStatus, target: CaseStatus) {
  const a = LAYOUT[source], b = LAYOUT[target]
  if (a.y === b.y && b.x > a.x && b.x - a.x <= STEP) return { sourceHandle: 'r', targetHandle: 'l', type: 'straight' }
  if (a.y === b.y) return { sourceHandle: 'ts', targetHandle: 'tt', type: 'arc' }
  return a.y < b.y ? { sourceHandle: 'bs', targetHandle: 'tt', type: 'default' } : { sourceHandle: 'ts', targetHandle: 'bt', type: 'default' }
}

interface Props {
  workflow: Workflow
  events: TimelineEvent[]
  status: CaseStatus
  working: boolean
  /** highlight the route taken at this step (from the timeline) */
  selectedSeq?: number | null
  /** draw the case as it was after this step (replay) */
  uptoSeq?: number
}

export default function FlowGraph({ workflow, events, status, working, selectedSeq, uptoSeq }: Props) {
  const { nodes, edges } = useMemo(() => {
    const path = transitions(events, uptoSeq)
    const count = new Map<string, number>()
    const visits = new Map<string, number>([['NEW', 1]])
    for (const t of path) {
      count.set(edgeId(t.from, t.to), (count.get(edgeId(t.from, t.to)) ?? 0) + 1)
      visits.set(t.to, (visits.get(t.to) ?? 0) + 1)
    }
    const last = path.at(-1)
    const selected = path.find((t) => t.seq === selectedSeq)

    const nodes: Node[] = workflow.nodes.map((n): StatusNode => ({
      id: n.id, type: 'status', position: LAYOUT[n.id], draggable: false,
      data: { label: n.label, actor: n.actor, visits: visits.get(n.id) ?? 0, current: n.id === status, working },
    }))
    nodes.push({ id: 'spacer', type: 'spacer', position: { x: LAYOUT.AUTO_REVIEW.x, y: -110 }, data: {}, selectable: false })

    // the map's edges, plus any route this case took that the map lacks (drawn dashed, so nothing is hidden)
    const all = [...workflow.edges]
    for (const t of path) {
      if (!all.some((e) => e.id === edgeId(t.from, t.to)))
        all.push({ id: edgeId(t.from, t.to), source: t.from, target: t.to, rules: t.rules, description: 'not on the map' })
    }
    let arcs = 0
    const edges: Edge[] = all.map((e) => {
      const drawn = shape(e.source, e.target)
      // each arc gets its own height (longer routes higher) so arcs never sit on top of each other
      const lift = drawn.type === 'arc' ? 40 + Math.abs(LAYOUT[e.target].x - LAYOUT[e.source].x) * 0.1 + 28 * arcs++ : 0
      const n = count.get(e.id) ?? 0
      const isSelected = selected && edgeId(selected.from, selected.to) === e.id
      const isLast = last && edgeId(last.from, last.to) === e.id
      const cls = ['flow-edge', n ? 'taken' : 'idle', isSelected ? 'selected' : '', isLast ? 'last' : ''].join(' ')
      return {
        id: e.id, source: e.source, target: e.target, ...drawn, data: { lift },
        className: cls, animated: !!(isLast && working),
        // only routes this case took are labelled; the rest stay faint (rules in the aria label)
        label: n ? `${path.filter((t) => edgeId(t.from, t.to) === e.id).flatMap((t) => t.rules)
          .filter((r, i, all) => all.indexOf(r) === i).join(' · ')}${n > 1 ? ` ×${n}` : ''}` : undefined,
        labelClassName: 'flow-edge-label',
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        zIndex: n ? 2 : 1,
        ariaLabel: `${e.source} to ${e.target}: ${e.rules.map((r) => `${r} (${RULES[r] ?? r})`).join('; ')}`,
      }
    })
    return { nodes, edges }
  }, [workflow, events, status, working, selectedSeq, uptoSeq])

  return (
    <div className="flow-wrap" aria-label="Case workflow diagram">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} colorMode="system" fitView
                 fitViewOptions={{ padding: 0.12 }} nodesDraggable={false} nodesConnectable={false}
                 elementsSelectable={false} zoomOnScroll={false} panOnScroll={false} preventScrolling={false}
                 minZoom={0.4}>
        <Background gap={20} size={1} />
      </ReactFlow>
      <div className="flow-legend small">
        <span><i className="lg taken" /> route taken</span>
        <span><i className="lg idle" /> possible route</span>
        <span><i className="lg now" /> current status</span>
      </div>
    </div>
  )
}
