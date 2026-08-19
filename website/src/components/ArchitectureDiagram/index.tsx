import {cloneElement, type ReactElement, type ReactNode} from 'react';
import clsx from 'clsx';
import {GENERIC_DIAGRAM, USE_CASES, type DiagramOutputLine} from '@site/src/data/useCases';
import styles from './styles.module.css';

/**
 * Native SVG redraw of the architecture figure (previously a static PNG).
 * Layout is grid-aligned by hand: three top-level regions share the same
 * y=16..496 band, connected by plain arrows; the two loop zones inside
 * VibeSys are dashed outlines (vs. solid-filled leaf cards) so nesting
 * reads without relying on hue, consistent with the site's monochrome
 * palette. Card heights track their actual content (single-line vs.
 * two-line title/subtitle) rather than a shared oversized default, and
 * every region starts its first row at the same y so the three columns
 * read as one grid.
 *
 * The USER INPUT and BESPOKE SYSTEM regions are data-driven off the
 * selected use case (see src/data/useCases.ts); the VibeSys region in
 * between is drawn once and never changes, since the loop is the part
 * that stays constant across targets. The use-case tabs live outside the
 * SVG, in a real HTML button column to its left, so selection keeps
 * native keyboard/focus behavior instead of hand-rolled SVG hit targets.
 */

function Icon({
  x,
  y,
  size = 22,
  children,
}: {
  x: number;
  y: number;
  size?: number;
  children: ReactNode;
}) {
  return (
    <svg
      x={x}
      y={y}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={styles.icon}>
      {children}
    </svg>
  );
}

/** A vendor/product mark, drawn monochrome (currentColor) to match the rest
 * of the diagram rather than each brand's own colors. Positioned by the
 * enclosing {@link LogoRow}. */
function Logo({size = 20, title, d}: {size?: number; title: string; d: string}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={styles.logo} role="img">
      <title>{title}</title>
      <path d={d} />
    </svg>
  );
}

function LogoRow({
  x,
  y,
  size = 20,
  gap = 8,
  highlight,
  children,
}: {
  x: number;
  y: number;
  size?: number;
  gap?: number;
  /** When set, dims every logo whose key doesn't match, to point at the
   * hardware the active use case actually targets. */
  highlight?: string;
  children: ReactElement<{size?: number}>[];
}) {
  return (
    <g>
      {children.map((child, i) => {
        const dimmed = highlight != null && child.key !== highlight;
        return (
          <g
            key={i}
            transform={`translate(${x + i * (size + gap)}, ${y})`}
            opacity={dimmed ? 0.25 : 1}>
            {cloneElement(child, {size})}
          </g>
        );
      })}
    </g>
  );
}

// Brand marks, simplified path data (single <path>, 0 0 24 24 viewBox),
// sourced from simple-icons. Rendered via currentColor, not brand color.
const LOGO_PATHS = {
  nvidia:
    'M8.948 8.798v-1.43a6.7 6.7 0 0 1 .424-.018c3.922-.124 6.493 3.374 6.493 3.374s-2.774 3.851-5.75 3.851c-.398 0-.787-.062-1.158-.185v-4.346c1.528.185 1.837.857 2.747 2.385l2.04-1.714s-1.492-1.952-4-1.952a6.016 6.016 0 0 0-.796.035m0-4.735v2.138l.424-.027c5.45-.185 9.01 4.47 9.01 4.47s-4.08 4.964-8.33 4.964c-.37 0-.733-.035-1.095-.097v1.325c.3.035.61.062.91.062 3.957 0 6.82-2.023 9.593-4.408.459.371 2.34 1.263 2.73 1.652-2.633 2.208-8.772 3.984-12.253 3.984-.335 0-.653-.018-.971-.053v1.864H24V4.063zm0 10.326v1.131c-3.657-.654-4.673-4.46-4.673-4.46s1.758-1.944 4.673-2.262v1.237H8.94c-1.528-.186-2.73 1.245-2.73 1.245s.68 2.412 2.739 3.11M2.456 10.9s2.164-3.197 6.5-3.533V6.201C4.153 6.59 0 10.653 0 10.653s2.35 6.802 8.948 7.42v-1.237c-4.84-.6-6.492-5.936-6.492-5.936z',
  amd: 'M18.324 9.137l1.559 1.56h2.556v2.557L24 14.814V9.137zM2 9.52l-2 4.96h1.309l.37-.982H3.9l.408.982h1.338L3.432 9.52zm4.209 0v4.955h1.238v-3.092l1.338 1.562h.188l1.338-1.556v3.091h1.238V9.52H10.47l-1.592 1.845L7.287 9.52zm6.283 0v4.96h2.057c1.979 0 2.88-1.046 2.88-2.472 0-1.36-.937-2.488-2.747-2.488zm1.237.91h.792c1.17 0 1.63.711 1.63 1.57 0 .728-.372 1.572-1.616 1.572h-.806zm-10.985.273l.791 1.932H2.008zm17.137.307l-1.604 1.603v2.25h2.246l1.604-1.607h-2.246z',
  intel:
    'M20.42 7.345v9.18h1.651v-9.18zM0 7.475v1.737h1.737V7.474zm9.78.352v6.053c0 .513.044.945.13 1.292.087.34.235.618.44.828.203.21.475.359.803.451.334.093.754.136 1.255.136h.216v-1.533c-.24 0-.445-.012-.593-.037a.672.672 0 0 1-.39-.173.693.693 0 0 1-.173-.377 4.002 4.002 0 0 1-.037-.606v-2.182h1.193v-1.416h-1.193V7.827zm-3.505 2.312c-.396 0-.76.08-1.082.241-.327.161-.6.384-.822.668l-.087.117v-.902H2.658v6.256h1.639v-3.214c.018-.588.16-1.02.433-1.299.29-.297.642-.445 1.044-.445.476 0 .841.149 1.082.433.235.284.359.686.359 1.2v3.324h1.663V12.97c.006-.89-.229-1.595-.686-2.09-.458-.495-1.1-.742-1.917-.742zm10.065.006a3.252 3.252 0 0 0-2.306.946c-.29.29-.525.637-.692 1.033a3.145 3.145 0 0 0-.254 1.273c0 .452.08.878.241 1.274.161.395.39.742.674 1.032.284.29.637.526 1.045.693.408.173.86.26 1.342.26 1.397 0 2.262-.637 2.782-1.23l-1.187-.904c-.248.297-.841.699-1.583.699-.464 0-.847-.105-1.138-.321a1.588 1.588 0 0 1-.593-.872l-.019-.056h4.915v-.587c0-.451-.08-.872-.235-1.267a3.393 3.393 0 0 0-.661-1.033 3.013 3.013 0 0 0-1.02-.692 3.345 3.345 0 0 0-1.311-.248zm-16.297.118v6.256h1.651v-6.256zm16.278 1.286c1.132 0 1.664.797 1.664 1.255l-3.32.006c0-.458.525-1.255 1.656-1.261z',
  apple:
    'M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701',
  amazon:
    'M.045 18.02c.072-.116.187-.124.348-.022 3.636 2.11 7.594 3.166 11.87 3.166 2.852 0 5.668-.533 8.447-1.595l.315-.14c.138-.06.234-.1.293-.13.226-.088.39-.046.525.13.12.174.09.336-.12.48-.256.19-.6.41-1.006.654-1.244.743-2.64 1.316-4.185 1.726a17.617 17.617 0 01-10.951-.577 17.88 17.88 0 01-5.43-3.35c-.1-.074-.151-.15-.151-.22 0-.047.021-.09.051-.13zm6.565-6.218c0-1.005.247-1.863.743-2.577.495-.71 1.17-1.25 2.04-1.615.796-.335 1.756-.575 2.912-.72.39-.046 1.033-.103 1.92-.174v-.37c0-.93-.105-1.558-.3-1.875-.302-.43-.78-.65-1.44-.65h-.182c-.48.046-.896.196-1.246.46-.35.27-.575.63-.675 1.096-.06.3-.206.465-.435.51l-2.52-.315c-.248-.06-.372-.18-.372-.39 0-.046.007-.09.022-.15.247-1.29.855-2.25 1.82-2.88.976-.616 2.1-.975 3.39-1.05h.54c1.65 0 2.957.434 3.888 1.29.135.15.27.3.405.48.12.165.224.314.283.45.075.134.15.33.195.57.06.254.105.42.135.51.03.104.062.3.076.615.01.313.02.493.02.553v5.28c0 .376.06.72.165 1.036.105.313.21.54.315.674l.51.674c.09.136.136.256.136.36 0 .12-.06.226-.18.314-1.2 1.05-1.86 1.62-1.963 1.71-.165.135-.375.15-.63.045a6.062 6.062 0 01-.526-.496l-.31-.347a9.391 9.391 0 01-.317-.42l-.3-.435c-.81.886-1.603 1.44-2.4 1.665-.494.15-1.093.227-1.83.227-1.11 0-2.04-.343-2.76-1.034-.72-.69-1.08-1.665-1.08-2.94l-.05-.076zm3.753-.438c0 .566.14 1.02.425 1.364.285.34.675.512 1.155.512.045 0 .106-.007.195-.02.09-.016.134-.023.166-.023.614-.16 1.08-.553 1.424-1.178.165-.28.285-.58.36-.91.09-.32.12-.59.135-.8.015-.195.015-.54.015-1.005v-.54c-.84 0-1.484.06-1.92.18-1.275.36-1.92 1.17-1.92 2.43l-.035-.02zm9.162 7.027c.03-.06.075-.11.132-.17.362-.243.714-.41 1.05-.5a8.094 8.094 0 011.612-.24c.14-.012.28 0 .41.03.65.06 1.05.168 1.172.33.063.09.099.228.099.39v.15c0 .51-.149 1.11-.424 1.8-.278.69-.664 1.248-1.156 1.68-.073.06-.14.09-.197.09-.03 0-.06 0-.09-.012-.09-.044-.107-.12-.064-.24.54-1.26.806-2.143.806-2.64 0-.15-.03-.27-.087-.344-.145-.166-.55-.257-1.224-.257-.243 0-.533.016-.87.046-.363.045-.7.09-1 .135-.09 0-.148-.014-.18-.044-.03-.03-.036-.047-.02-.077 0-.017.006-.03.02-.063v-.06z',
  docker:
    'M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.185.185.186m-2.93 0h2.12a.186.186 0 00.184-.186V6.29a.185.185 0 00-.185-.185H8.1a.185.185 0 00-.185.185v1.887c0 .102.083.185.185.186m-2.964 0h2.119a.186.186 0 00.185-.186V6.29a.185.185 0 00-.185-.185H5.136a.186.186 0 00-.186.185v1.887c0 .102.084.185.186.186m5.893 2.715h2.118a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m-2.93 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.083.185.185.185m-2.964 0h2.119a.185.185 0 00.185-.185V9.006a.185.185 0 00-.184-.186h-2.12a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185m-2.92 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.082.185.185.185M23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.226.327c-.284.438-.49.922-.612 1.43-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 00-.75.748 11.376 11.376 0 00.692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.723 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.248 12.248 0 003.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288z',
  modal:
    'M4.89 5.57 0 14.002l2.521 4.4h5.05l4.396-7.718 4.512 7.709 4.996.037L24 14.057l-4.857-8.452-5.073-.015-2.076 3.598L9.94 5.57Zm.837.729h3.787l1.845 3.252H7.572Zm9.189.021 3.803.012 4.228 7.355-3.736-.027zm-9.82.346L6.94 9.914l-4.209 7.389-1.892-3.3Zm9.187.014 4.297 7.343-1.892 3.282-4.3-7.344zm-6.713 3.6h3.79l-4.212 7.394H3.361Zm11.64 4.109 3.74.027-1.893 3.281-3.74-.027z',
};

function LeafCard({
  x,
  y,
  w,
  h,
  icon,
  title,
  subtitle,
  logoRow,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  icon: ReactNode;
  title: string[];
  subtitle?: string[];
  /** Vendor/product marks shown as a row below the title and subtitle,
   * e.g. the hardware backends or execution environments a card covers.
   * `y` is an absolute diagram coordinate, hand-placed like everything
   * else in this file. */
  logoRow?: {
    y: number;
    size?: number;
    gap?: number;
    highlight?: string;
    items: ReactElement<{size?: number}>[];
  };
}) {
  const iconX = x + 13;
  const iconY = y + 11;
  const textX = x + 13;
  let ty = iconY + 22 + 19;
  const titleLines = title.map((line, i) => {
    const el = (
      <tspan key={i} x={textX} y={ty}>
        {line}
      </tspan>
    );
    ty += 18;
    return el;
  });
  ty += 3;
  const subtitleLines = (subtitle ?? []).map((line, i) => {
    const el = (
      <tspan key={i} x={textX} y={ty}>
        {line}
      </tspan>
    );
    ty += 16;
    return el;
  });

  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={6} className={styles.card} />
      <Icon x={iconX} y={iconY}>
        {icon}
      </Icon>
      <text className={styles.cardTitle}>{titleLines}</text>
      {subtitle && <text className={styles.cardSubtitle}>{subtitleLines}</text>}
      {logoRow && (
        <LogoRow x={textX} y={logoRow.y} size={logoRow.size} gap={logoRow.gap} highlight={logoRow.highlight}>
          {logoRow.items}
        </LogoRow>
      )}
    </g>
  );
}

/** Renders the BESPOKE SYSTEM file tree from a use case's output lines.
 * `dir`/`note` lines start a new visual group (extra leading space);
 * `file` lines are dim; `file-new`/`file-modified` are bold so a changed
 * or added file stands out against the rest of an otherwise-untouched tree.
 * `startY` shifts down when a stat block occupies the top of the card. */
function OutputTree({lines, startY}: {lines: DiagramOutputLine[]; startY: number}) {
  return (
    <text x={880} y={startY} className={styles.mono}>
      {lines.map((line, i) => {
        const step = i === 0 ? 0 : line.kind === 'dir' || line.kind === 'note' ? 27 : 19;
        const bold = line.kind === 'dir' || line.kind === 'file-new' || line.kind === 'file-modified';
        const dimClass =
          line.kind === 'file' ? styles.monoDim : line.kind === 'note' ? styles.monoNote : undefined;
        return (
          <tspan key={i} x={880} dy={step} fontWeight={bold ? 600 : undefined} className={dimClass}>
            {line.text}
          </tspan>
        );
      })}
    </text>
  );
}

/** Performance-benefit callout at the top of the BESPOKE SYSTEM card: the
 * metric, its context, and (for paper-backed cases) a link to the source.
 * Only rendered when a use case is selected; the generic state has no
 * result to show, so the file tree alone fills the card. */
function StatBlock({
  metric,
  metricLabel,
  link,
}: {
  metric: string;
  metricLabel: string[];
  link?: {label: string; href: string};
}) {
  const labelY = 118;
  const linkY = labelY + metricLabel.length * 14 + 8;
  return (
    <g>
      <rect x={864} y={62} width={264} height={104} rx={6} className={styles.card} />
      <text x={880} y={96} className={styles.statMetric}>
        {metric}
      </text>
      <text x={880} y={labelY} className={styles.statLabel}>
        {metricLabel.map((line, i) => (
          <tspan key={i} x={880} dy={i === 0 ? 0 : 14}>
            {line}
          </tspan>
        ))}
      </text>
      {link && (
        <a href={link.href} target="_blank" rel="noopener noreferrer">
          <text x={880} y={linkY} className={styles.statLink}>
            {link.label} →
          </text>
        </a>
      )}
    </g>
  );
}

const DEFAULT_CAPTION =
  'An outer loop searches over designs; an inner loop implements and validates each one.';

export default function ArchitectureDiagram({
  activeId,
  onSelect,
}: {
  activeId: string | null;
  onSelect: (id: string | null) => void;
}): ReactNode {
  const useCase = USE_CASES.find((candidate) => candidate.id === activeId) ?? null;
  const diagram = useCase?.diagram ?? GENERIC_DIAGRAM;
  const treeY = useCase ? 178 : 62;
  const treeH = useCase ? 302 : 418;

  return (
    <figure className={styles.figure}>
      <figcaption className={styles.caption}>{useCase?.description ?? DEFAULT_CAPTION}</figcaption>
      <div className={styles.diagramRow}>
        <div className={styles.tabColumn} role="tablist" aria-label="Use case">
          <span className={styles.tabColumnLabel}>USE CASES</span>
          <div className={styles.tabList}>
            {USE_CASES.map((candidate) => {
              const active = candidate.id === activeId;
              return (
                <button
                  key={candidate.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={clsx(styles.tab, active && styles.tabActive)}
                  onClick={() => onSelect(active ? null : candidate.id)}>
                  <span className={styles.tabText}>
                    <span className={styles.tabTag}>{candidate.tag}</span>
                    <span className={styles.tabHeadline}>{candidate.headline}</span>
                  </span>
                  <span className={styles.tabArrow} aria-hidden="true">
                    →
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <svg
          className={styles.diagram}
          viewBox="0 0 1160 512"
          role="img"
          aria-label="Diagram: user input (model, hardware, workload) feeds VibeSys, where an outer loop plans the search and an inner loop of Implementer, Accuracy Judge, and Profiler implements, checks, and benchmarks each candidate, producing a bespoke system. On error, the Accuracy Judge sends the candidate back to the Implementer.">
          <defs>
          <marker
            id="arch-arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" className={styles.arrowHead} />
          </marker>
        </defs>

        {/* ---- Region A: User Input ---- */}
        <rect x={16} y={16} width={216} height={480} rx={14} className={styles.regionOutline} />
        <text x={32} y={44} className={styles.zoneLabel}>
          USER INPUT
        </text>

        <LeafCard
          x={32}
          y={62}
          w={184}
          h={108}
          title={['Starting point']}
          subtitle={diagram.startingPoint}
          icon={
            <>
              <circle cx={12} cy={5.5} r={2} />
              <circle cx={5.5} cy={18} r={2} />
              <circle cx={18.5} cy={18} r={2} />
              <path d="M10.8 7.3L7 15.5M13.2 7.3l3.8 8.2" />
            </>
          }
        />
        <LeafCard
          x={32}
          y={211}
          w={184}
          h={120}
          title={['Hardware']}
          subtitle={diagram.hardware}
          icon={
            <>
              <rect x={6} y={6} width={12} height={12} rx={1.5} />
              <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
            </>
          }
          logoRow={{
            y: 298,
            size: 20,
            gap: 9,
            highlight: diagram.hardwareVendor,
            items: [
              <Logo key="nvidia" title="NVIDIA" d={LOGO_PATHS.nvidia} />,
              <Logo key="amd" title="AMD" d={LOGO_PATHS.amd} />,
              <Logo key="intel" title="Intel" d={LOGO_PATHS.intel} />,
              <Logo key="apple" title="Apple" d={LOGO_PATHS.apple} />,
              <Logo key="amazon" title="Amazon" d={LOGO_PATHS.amazon} />,
            ],
          }}
        />
        <LeafCard
          x={32}
          y={372}
          w={184}
          h={108}
          title={['Workload']}
          subtitle={diagram.workload}
          icon={
            <>
              <rect x={4.5} y={13} width={3.4} height={7} fill="currentColor" stroke="none" />
              <rect x={10.3} y={9} width={3.4} height={11} fill="currentColor" stroke="none" />
              <rect x={16.1} y={4} width={3.4} height={16} fill="currentColor" stroke="none" />
            </>
          }
        />

        {/* arrow: User Input -> VibeSys */}
        <line x1={236} y1={256} x2={256} y2={256} className={styles.arrow} markerEnd="url(#arch-arrow)" />

        {/* ---- Region B: VibeSys ---- */}
        <rect x={260} y={16} width={560} height={480} rx={14} className={styles.regionOutline} />
        <text x={276} y={52} className={styles.title}>
          VibeSys
        </text>

        {/* Outer Loop zone */}
        <rect x={276} y={70} width={528} height={158} rx={10} className={styles.zoneOutline} />
        <text x={288} y={88} className={styles.zoneLabel}>
          OUTER LOOP
        </text>
        <LeafCard
          x={292}
          y={100}
          w={248}
          h={112}
          title={['Search Policy']}
          subtitle={['Design decisions', 'Branch / backtrack']}
          icon={
            <>
              <circle cx={6} cy={6} r={2} />
              <circle cx={6} cy={18} r={2} />
              <circle cx={18} cy={6} r={2} />
              <path d="M6 8v8M8 6h8a2 2 0 0 1-2 2h-2a4 4 0 0 0-4 4" />
            </>
          }
        />
        <LeafCard
          x={556}
          y={100}
          w={232}
          h={112}
          title={['Search State']}
          subtitle={['Git state', 'Feedback history']}
          icon={
            <>
              <ellipse cx={12} cy={6} rx={7} ry={3} />
              <path d="M5 6v12a7 3 0 0 0 14 0V6" />
              <path d="M5 12a7 3 0 0 0 14 0" />
            </>
          }
        />
        <line
          x1={554}
          y1={156}
          x2={542}
          y2={156}
          className={styles.arrowMuted}
          markerEnd="url(#arch-arrow)"
        />

        {/* Base Commit + Task / Commit + Feedback connectors */}
        <text x={400} y={242} textAnchor="middle" className={styles.flowLabel}>
          Base Commit + Task
        </text>
        <line x1={400} y1={246} x2={400} y2={259} className={styles.arrow} markerEnd="url(#arch-arrow)" />
        <text x={680} y={242} textAnchor="middle" className={styles.flowLabel}>
          Commit + Feedback
        </text>
        <line x1={680} y1={259} x2={680} y2={246} className={styles.arrow} markerEnd="url(#arch-arrow)" />

        {/* Inner Loop zone */}
        <rect x={276} y={264} width={528} height={122} rx={10} className={styles.zoneOutline} />
        <text x={288} y={282} className={styles.zoneLabel}>
          INNER LOOP
        </text>

        {/* Error: Accuracy Judge sends the candidate back to the Implementer */}
        <path
          d="M540 294 C 500 280, 407 280, 367 294"
          className={styles.arrowDashed}
          markerEnd="url(#arch-arrow)"
        />
        <text x={454} y={276} textAnchor="middle" className={styles.errorLabel}>
          Error
        </text>

        <LeafCard
          x={292}
          y={294}
          w={150}
          h={76}
          title={['Implementer']}
          icon={<path d="M8 6L3 12l5 6M16 6l5 6-5 6" />}
        />
        <LeafCard
          x={458}
          y={294}
          w={165}
          h={76}
          title={['Accuracy Judge']}
          icon={
            <>
              <circle cx={12} cy={12} r={8} />
              <path d="M8.5 12.5l2.5 2.5 5-5" />
            </>
          }
        />
        <LeafCard
          x={639}
          y={294}
          w={150}
          h={76}
          title={['Profiler']}
          icon={
            <>
              <path d="M4 16a8 8 0 0 1 16 0" />
              <path d="M12 16l4-5" />
              <circle cx={12} cy={16} r={1.3} fill="currentColor" stroke="none" />
            </>
          }
        />
        <line x1={442} y1={332} x2={458} y2={332} className={styles.arrowMuted} markerEnd="url(#arch-arrow)" />
        <line x1={623} y1={332} x2={639} y2={332} className={styles.arrowMuted} markerEnd="url(#arch-arrow)" />

        <LeafCard
          x={292}
          y={394}
          w={248}
          h={86}
          title={['Skills Library']}
          icon={
            <>
              <path d="M6 3h9l4 4v14H6z" />
              <path d="M9 11h6M9 14h6M9 17h4" />
            </>
          }
        />
        <LeafCard
          x={556}
          y={394}
          w={232}
          h={86}
          title={['Execution Environment']}
          icon={
            <>
              <rect x={3} y={4} width={18} height={16} rx={1} />
              <path d="M7 9l3 3-3 3M13 15h4" />
            </>
          }
          logoRow={{
            y: 456,
            size: 18,
            gap: 10,
            items: [
              <Logo key="docker" title="Docker" d={LOGO_PATHS.docker} />,
              <Logo key="modal" title="Modal" d={LOGO_PATHS.modal} />,
            ],
          }}
        />
        <line
          x1={367}
          y1={394}
          x2={367}
          y2={370}
          className={styles.arrowMuted}
          markerEnd="url(#arch-arrow)"
        />
        <line
          x1={590}
          y1={394}
          x2={590}
          y2={370}
          className={styles.arrowMuted}
          markerStart="url(#arch-arrow)"
          markerEnd="url(#arch-arrow)"
        />
        <line
          x1={740}
          y1={394}
          x2={740}
          y2={370}
          className={styles.arrowMuted}
          markerStart="url(#arch-arrow)"
          markerEnd="url(#arch-arrow)"
        />

        {/* arrow: VibeSys -> Bespoke System */}
        <line x1={824} y1={256} x2={844} y2={256} className={styles.arrow} markerEnd="url(#arch-arrow)" />

          {/* ---- Region C: Bespoke System ---- */}
          <rect x={848} y={16} width={296} height={480} rx={14} className={styles.regionOutline} />
          <text x={864} y={44} className={styles.zoneLabel}>
            BESPOKE SYSTEM
          </text>
          {useCase && (
            <StatBlock metric={useCase.metric} metricLabel={useCase.metricLabel} link={useCase.link} />
          )}
          <rect x={864} y={treeY} width={264} height={treeH} rx={6} className={styles.card} />
          <OutputTree lines={diagram.output} startY={treeY + 28} />
        </svg>
      </div>
    </figure>
  );
}
