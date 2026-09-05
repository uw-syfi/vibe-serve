import type {AgentPhase} from '@vibesys/core-state';

/**
 * Layout for the agent graph drawn in the Agents pane.
 *
 * The pane shows one column per agent kind, left to right in the order the
 * loop runs them, with the agents of a kind stacked inside their column. Today
 * a round has one phase per kind, so the graph is a chain; the layout is
 * written for several agents per kind because that is where the loop is going,
 * and a stage that grows only adds rows to its column.
 *
 * This module is pure geometry: it turns phases plus an available width into
 * cell coordinates. The renderer owns colors and renderables.
 */

/**
 * Border plus one content row each for the kind, the status, and the agent
 * runtime (harness/model), whether or not the runtime row has anything to
 * show. A fixed height keeps every node the same size instead of shifting the
 * layout for nodes that lack runtime identity.
 */
export const NODE_HEIGHT = 5;
/** Blank row between stacked agents in the same column. */
const ROW_GAP = 2;
/**
 * Columns between one node's right edge and the next node's left edge. Three
 * of them are lanes for vertical runs, one is the arrow head, and one keeps the
 * arrow off the border.
 */
const GUTTER = 5;
/** Narrower than this and a kind is unreadable even truncated. */
const NODE_WIDTH_MIN = 14;
/** Wider adds padding, not information. */
const NODE_WIDTH_MAX = 18;

export interface GraphNode {
  phase: AgentPhase;
  /** Cell of the node's top-left corner, relative to the graph area. */
  x: number;
  y: number;
  width: number;
}

/** How an edge cell should be colored, decided from the phases it connects. */
export type EdgeTone = 'idle' | 'done' | 'live' | 'failed';

export interface GraphCell {
  x: number;
  y: number;
  glyph: string;
  tone: EdgeTone;
}

export interface AgentGraph {
  nodes: GraphNode[];
  cells: GraphCell[];
  width: number;
  height: number;
}

/** Border on both sides plus one column of padding on both sides. */
const PANE_CHROME = 4;

/**
 * Pane widths that can draw `stageCount` stages: `min` is the narrowest the
 * graph stays readable at, `max` the point past which extra columns add
 * padding rather than information. Callers compare `min` against the room they
 * have before choosing the graph over the stacked list.
 */
export function graphPaneBounds(stageCount: number): {min: number; max: number} {
  const stages = Math.max(1, stageCount);
  const gutters = (stages - 1) * GUTTER;
  return {
    min: stages * NODE_WIDTH_MIN + gutters + PANE_CHROME,
    max: stages * NODE_WIDTH_MAX + gutters + PANE_CHROME,
  };
}

/** Agent kinds in the order the round first mentions them. */
export function stageKinds(phases: AgentPhase[]): string[] {
  const kinds: string[] = [];
  for (const phase of phases) if (!kinds.includes(phase.kind)) kinds.push(phase.kind);
  return kinds;
}

export interface GraphWindow {
  phases: AgentPhase[];
  /** Agents the rows could not hold; 0 when the whole round is on screen. */
  hidden: number;
}

/**
 * The phases that fit in `rows`, and the count of those that do not.
 *
 * A stage stacks: an interrupted attempt and the attempt that replaced it are
 * two agents in one column, so a round can be taller than the pane. The layout
 * itself is unbounded, so something has to decide what is on screen, and a
 * graph that silently ran off the bottom is what this replaces. Each column
 * keeps its newest agents, because the live attempt is the one an operator is
 * watching, and the count says what was left behind. One row is reserved for
 * that count when there is one, so the indicator never sits on a node.
 */
export function graphWindow(phases: AgentPhase[], rows: number): GraphWindow {
  const full = fitColumns(phases, rows);
  if (full.hidden === 0) return full;
  return fitColumns(phases, rows - 1);
}

/** Agents a column can stack in `rows` rows, 0 when not even one fits. */
function columnCapacity(rows: number): number {
  return Math.max(0, Math.floor((rows + ROW_GAP) / (NODE_HEIGHT + ROW_GAP)));
}

function fitColumns(phases: AgentPhase[], rows: number): GraphWindow {
  const capacity = columnCapacity(rows);
  const dropped = new Set<AgentPhase>();
  for (const kind of stageKinds(phases)) {
    const column = phases.filter(phase => phase.kind === kind);
    for (const phase of column.slice(0, Math.max(0, column.length - capacity))) {
      dropped.add(phase);
    }
  }
  return {phases: phases.filter(phase => !dropped.has(phase)), hidden: dropped.size};
}

export function layoutAgentGraph(phases: AgentPhase[], availableWidth: number): AgentGraph {
  const kinds = stageKinds(phases);
  const columns = kinds.map(kind => phases.filter(phase => phase.kind === kind));
  const nodeWidth = fitNodeWidth(kinds.length, availableWidth);
  const pitch = nodeWidth + GUTTER;
  const tallest = Math.max(...columns.map(column => columnHeight(column.length)), 0);

  const nodes: GraphNode[] = [];
  for (const [index, column] of columns.entries()) {
    // Shorter stages centre against the tallest one, which is what a layered
    // graph looks like and keeps the edges near horizontal.
    const top = Math.floor((tallest - columnHeight(column.length)) / 2);
    for (const [row, phase] of column.entries()) {
      nodes.push({
        phase,
        x: index * pitch,
        y: top + row * (NODE_HEIGHT + ROW_GAP),
        width: nodeWidth,
      });
    }
  }

  const cells = routeEdges(columns, nodes, nodeWidth, pitch);
  const width = kinds.length === 0 ? 0 : (kinds.length - 1) * pitch + nodeWidth;
  return {nodes, cells, width, height: tallest};
}

function columnHeight(count: number): number {
  return count === 0 ? 0 : count * NODE_HEIGHT + (count - 1) * ROW_GAP;
}

function fitNodeWidth(stageCount: number, availableWidth: number): number {
  if (stageCount <= 0) return NODE_WIDTH_MIN;
  const room = availableWidth - (stageCount - 1) * GUTTER;
  const fitted = Math.floor(room / stageCount);
  return Math.max(NODE_WIDTH_MIN, Math.min(NODE_WIDTH_MAX, fitted));
}

const UP = 1;
const RIGHT = 2;
const DOWN = 4;
const LEFT = 8;

/**
 * Box-drawing glyph for a cell, keyed by the directions that leave it. Edges
 * that cross or merge resolve to a junction instead of one overwriting the
 * other.
 */
const JUNCTION: Record<number, string> = {
  [UP]: '│',
  [DOWN]: '│',
  [LEFT]: '─',
  [RIGHT]: '─',
  [UP | DOWN]: '│',
  [LEFT | RIGHT]: '─',
  [UP | RIGHT]: '└',
  [UP | LEFT]: '┘',
  [DOWN | RIGHT]: '┌',
  [DOWN | LEFT]: '┐',
  [UP | DOWN | RIGHT]: '├',
  [UP | DOWN | LEFT]: '┤',
  [UP | LEFT | RIGHT]: '┴',
  [DOWN | LEFT | RIGHT]: '┬',
  [UP | DOWN | LEFT | RIGHT]: '┼',
};

const TONE_RANK: Record<EdgeTone, number> = {idle: 0, done: 1, live: 2, failed: 3};

function routeEdges(
  columns: AgentPhase[][],
  nodes: GraphNode[],
  nodeWidth: number,
  pitch: number,
): GraphCell[] {
  const cellAt = new Map<string, {mask: number; tone: EdgeTone; glyph: string}>();
  const nodeOf = (phase: AgentPhase): GraphNode =>
    nodes[nodes.findIndex(node => node.phase === phase)] as GraphNode;

  for (const [index, column] of columns.entries()) {
    const next = columns[index + 1];
    if (next === undefined) continue;
    const x1 = index * pitch + nodeWidth;
    const x2 = x1 + GUTTER - 1;
    const lanes = laneOrder(x1 + 1, x2 - 1);
    const taken: Array<Array<[number, number]>> = lanes.map(() => []);

    for (const source of column) {
      const from = nodeOf(source);
      const sourceY = from.y + 1;
      const targetYs = next.map(target => nodeOf(target).y + 1);
      const lane = claimLane(
        lanes,
        taken,
        Math.min(sourceY, ...targetYs),
        Math.max(sourceY, ...targetYs),
      );
      for (const target of next) {
        const to = nodeOf(target);
        const targetY = to.y + 1;
        const tone = edgeTone(source, target);
        const points: Array<[number, number]> =
          sourceY === targetY
            ? [
                [x1, sourceY],
                [x2, targetY],
              ]
            : [
                [x1, sourceY],
                [lane, sourceY],
                [lane, targetY],
                [x2, targetY],
              ];
        paint(cellAt, points, tone);
        // The arrow head replaces the junction glyph: it is the one cell that
        // has to say which way the handover went.
        cellAt.set(`${x2},${targetY}`, {mask: 0, tone, glyph: '▶'});
      }
    }
  }

  return [...cellAt.entries()].map(([key, value]) => {
    const [x, y] = key.split(',').map(Number);
    return {x: x as number, y: y as number, glyph: value.glyph, tone: value.tone};
  });
}

/** Centre lane first, so a single chain draws a straight line. */
function laneOrder(low: number, high: number): number[] {
  const lanes: number[] = [];
  for (let lane = low; lane <= high; lane += 1) lanes.push(lane);
  const middle = Math.floor((low + high) / 2);
  return lanes.sort((a, b) => Math.abs(a - middle) - Math.abs(b - middle) || a - b);
}

/**
 * Edges leaving one node share a lane. Edges from different nodes take
 * different lanes when their row ranges overlap, so two unrelated handovers
 * never merge into a line that reads as a connection.
 */
function claimLane(
  lanes: number[],
  taken: Array<Array<[number, number]>>,
  low: number,
  high: number,
): number {
  for (const [index, lane] of lanes.entries()) {
    const rows = taken[index];
    if (rows === undefined) continue;
    if (rows.every(([from, to]) => high < from || low > to)) {
      rows.push([low, high]);
      return lane;
    }
  }
  return lanes[0] ?? low;
}

function paint(
  cellAt: Map<string, {mask: number; tone: EdgeTone; glyph: string}>,
  points: Array<[number, number]>,
  tone: EdgeTone,
): void {
  const cells = expand(points);
  for (const [index, cell] of cells.entries()) {
    let mask = 0;
    const previous = cells[index - 1];
    const next = cells[index + 1];
    if (previous) mask |= direction(previous[0] - cell[0], previous[1] - cell[1]);
    if (next) mask |= direction(next[0] - cell[0], next[1] - cell[1]);
    const key = `${cell[0]},${cell[1]}`;
    const existing = cellAt.get(key);
    const merged = (existing?.mask ?? 0) | mask;
    const winner =
      existing === undefined || TONE_RANK[tone] >= TONE_RANK[existing.tone] ? tone : existing.tone;
    cellAt.set(key, {mask: merged, tone: winner, glyph: JUNCTION[merged] ?? '┼'});
  }
}

function expand(points: Array<[number, number]>): Array<[number, number]> {
  const cells: Array<[number, number]> = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index] as [number, number];
    const to = points[index + 1] as [number, number];
    const stepX = Math.sign(to[0] - from[0]);
    const stepY = Math.sign(to[1] - from[1]);
    let [x, y] = from;
    while (x !== to[0] || y !== to[1]) {
      cells.push([x, y]);
      x += stepX;
      y += stepY;
    }
  }
  const last = points.at(-1);
  if (last) cells.push(last);
  return cells;
}

function direction(deltaX: number, deltaY: number): number {
  if (deltaX > 0) return RIGHT;
  if (deltaX < 0) return LEFT;
  if (deltaY > 0) return DOWN;
  return UP;
}

function edgeTone(source: AgentPhase, target: AgentPhase): EdgeTone {
  if (
    source.status === 'failed' ||
    source.status === 'cancelled' ||
    source.status === 'interrupted'
  )
    return 'failed';
  if (source.status === 'active' || (source.status === 'completed' && target.status === 'active')) {
    return 'live';
  }
  if (source.status === 'completed' && target.status !== 'pending') return 'done';
  return 'idle';
}
