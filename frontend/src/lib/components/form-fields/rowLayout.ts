const DEFAULT_COLUMNS = 2;

// Settings pane content width is fixed per viewport tier now (see
// generationLayout.ts's settingsPaneContentWidth): 348/388/428px. 300 sits
// below all three, so this default only fires for a row whose own
// `collapse_at` override sits above the narrowest tier.
const DEFAULT_COLLAPSE_AT = 300;

const MAX_ROW_TRACKS = 6;

const FRACTION_WIDTH_RE = /^\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*$/;

export interface RowLayout {
	columns: number;
	collapseAt: number;
	activeColumns: number;
	gridTemplateColumns: string;
	weighted: boolean;
	trackCountCapped: boolean;
}

function hasUsableWidth(width: unknown): boolean {
	if (typeof width === 'number') {
		return Number.isFinite(width) && width > 0;
	}
	if (typeof width === 'string') {
		const match = FRACTION_WIDTH_RE.exec(width);
		if (!match) return false;
		return Number(match[1]) > 0 && Number(match[2]) > 0;
	}
	return false;
}

/** Parses a preset-authored `width` per the shared frontend/backend contract:
 * a positive number is the weight as-is, a string `"a/b"` with positive a/b is
 * the weight a/b, anything else (absent, null, unparseable) is weight 1. */
export function parseFieldWidth(width: unknown): number {
	if (!hasUsableWidth(width)) return 1;
	if (typeof width === 'number') return width;
	const match = FRACTION_WIDTH_RE.exec(width as string);
	return Number(match![1]) / Number(match![2]);
}

/** Resolves RowField's responsive grid without coupling its breakpoint rules to
 * ResizeObserver. Configuration precedence deliberately matches the existing
 * row contract. */
export function resolveRowLayout(config: any, containerWidth: number): RowLayout {
	const rowConfig = config.configuration || {};
	const columns = Math.max(1, Math.min(6, Number(rowConfig.columns ?? config.columns ?? DEFAULT_COLUMNS) || DEFAULT_COLUMNS));
	const collapseAt = Math.max(
		0,
		Number(rowConfig.collapse_at ?? rowConfig.collapseAt ?? config.collapse_at ?? config.collapseAt ?? DEFAULT_COLLAPSE_AT) || DEFAULT_COLLAPSE_AT
	);
	const activeColumns = containerWidth > 0 && containerWidth >= collapseAt ? columns : 1;

	const children: any[] = Array.isArray(config.children) ? config.children : [];
	const weighted = activeColumns > 1 && children.some((child) => hasUsableWidth(child?.width));
	const trackCountCapped = weighted && children.length > MAX_ROW_TRACKS;

	const gridTemplateColumns = weighted
		? children
				.slice(0, MAX_ROW_TRACKS)
				.map((child) => `minmax(0, ${parseFieldWidth(child?.width)}fr)`)
				.join(' ')
		: `repeat(${activeColumns}, minmax(0, 1fr))`;

	return { columns, collapseAt, activeColumns, gridTemplateColumns, weighted, trackCountCapped };
}
