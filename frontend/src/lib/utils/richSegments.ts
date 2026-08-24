import type {
	ChipData,
	RichSegment,
	SavedSegment,
	Segment,
	SegmentCategory,
	SegmentTemplate
} from '$lib/types/segments';
import { richTextToPlainText } from '$lib/utils/richTextUtils';
import { randomUUID } from '$lib/utils/uuid';

export type SegmentApplyMode = 'append' | 'prepend' | 'replace';
export type SegmentIdFactory = () => string;

export function createEditorSegmentId(): string {
	return randomUUID();
}

export function cloneChipData(chip: ChipData): ChipData {
	return {
		...chip,
		allValues: chip.allValues.map((value) => ({ ...value }))
	};
}

export function cloneChips(chips: Record<string, ChipData> = {}): Record<string, ChipData> {
	return Object.fromEntries(Object.entries(chips).map(([id, chip]) => [id, cloneChipData(chip)]));
}

/** Legacy editor sessions may only carry `isDisabled`; the persistent contract uses `enabled`. */
export function isSegmentEnabled(segment: Pick<Segment, 'enabled' | 'isDisabled'> | RichSegment): boolean {
	if (typeof segment.enabled === 'boolean') return segment.enabled;
	return !('isDisabled' in segment && segment.isDisabled);
}

/** Strip editor-only fields and return the exact persistent composition contract. */
export function toRichSegment(segment: Segment | RichSegment): RichSegment {
	const editorSegment = segment as Segment;
	return {
		type: segment.type === 'break' ? 'break' : 'content',
		content: segment.content ?? '',
		chips: cloneChips(segment.chips || {}),
		enabled: isSegmentEnabled(segment),
		...((segment.name ?? editorSegment.title) ? { name: (segment.name ?? editorSegment.title) as string } : {}),
		...(segment.color ? { color: segment.color } : {}),
		...(segment.description ? { description: segment.description } : {})
	};
}

/** Create a detached editor copy with a fresh local id and independent nested chip state. */
export function toEditorSegment(
	segment: Segment | RichSegment,
	idFactory: SegmentIdFactory = createEditorSegmentId,
	template?: Segment['template']
): Segment {
	const rich = toRichSegment(segment);
	return {
		id: idFactory(),
		type: rich.type,
		content: rich.content,
		chips: cloneChips(rich.chips),
		enabled: rich.enabled,
		// Keep generation/session callers correct until all legacy readers migrate.
		isDisabled: !rich.enabled,
		...(rich.name ? { name: rich.name } : {}),
		...(rich.color ? { color: rich.color } : {}),
		...(rich.description ? { description: rich.description } : {}),
		...(template ? { template } : {})
	};
}

export function createBlankEditorSegment(idFactory: SegmentIdFactory = createEditorSegmentId): Segment {
	return toEditorSegment(
		{
			type: 'content',
			content: '',
			chips: {},
			enabled: true
		},
		idFactory
	);
}

export function cloneEditorSegments(
	segments: readonly (Segment | RichSegment)[],
	idFactory: SegmentIdFactory = createEditorSegmentId
): Segment[] {
	return segments.map((segment) => toEditorSegment(segment, idFactory));
}

export function ensureSegmentList(
	segments: readonly Segment[] | null | undefined,
	idFactory: SegmentIdFactory = createEditorSegmentId
): Segment[] {
	return segments && segments.length > 0 ? [...segments] : [createBlankEditorSegment(idFactory)];
}

export function isPristineBlankSegment(segment: Segment): boolean {
	return (
		(segment.type ?? 'content') === 'content' &&
		(segment.content || '').trim() === '' &&
		Object.keys(segment.chips || {}).length === 0 &&
		isSegmentEnabled(segment) &&
		!(segment.name || segment.title || '').trim() &&
		!(segment.color || '').trim() &&
		!(segment.description || '').trim()
	);
}

/** Only the editor's single untouched placeholder represents an empty target list. */
export function isPristinePlaceholderList(segments: readonly Segment[]): boolean {
	return segments.length === 1 && isPristineBlankSegment(segments[0]);
}

export function hasMeaningfulSegments(segments: readonly Segment[]): boolean {
	return segments.length > 0 && !isPristinePlaceholderList(segments);
}

function mergeSegmentLists(base: Segment[], copies: Segment[], mode: SegmentApplyMode): Segment[] {
	switch (mode) {
		case 'prepend':
			return [...copies, ...base];
		case 'replace':
			return copies;
		case 'append':
		default:
			return [...base, ...copies];
	}
}

/**
 * Apply a Prompt or Segment Template to exactly one editor list.
 * Existing cards keep their editor ids; every incoming card is a detached deep copy.
 */
export function applySegmentList(
	target: readonly Segment[],
	incoming: readonly (Segment | RichSegment)[],
	mode: SegmentApplyMode,
	idFactory: SegmentIdFactory = createEditorSegmentId
): Segment[] {
	const base = isPristinePlaceholderList(target) ? [] : [...target];
	const copies = cloneEditorSegments(incoming, idFactory);
	return ensureSegmentList(mergeSegmentLists(base, copies, mode), idFactory);
}

/**
 * Apply a Segment Template, stamping each created card with `template` provenance
 * (`{ id, name, slot, position }`) linking it back to the template slot it came from.
 */
export function applyTemplateSegments(
	target: readonly Segment[],
	template: Pick<SegmentTemplate, 'id' | 'name' | 'segments'>,
	mode: SegmentApplyMode,
	idFactory: SegmentIdFactory = createEditorSegmentId
): Segment[] {
	const base = isPristinePlaceholderList(target) ? [] : [...target];
	const copies = template.segments.map((slot, position) =>
		toEditorSegment(slot, idFactory, {
			id: template.id,
			name: template.name,
			slot: slot.name || `Segment ${position + 1}`,
			position
		})
	);
	return ensureSegmentList(mergeSegmentLists(base, copies, mode), idFactory);
}

export function effectiveSavedSegmentColor(
	segment: Pick<SavedSegment, 'color' | 'effective_color'>,
	category?: Pick<SegmentCategory, 'color'> | null
): string | undefined {
	return segment.color || segment.effective_color || category?.color || undefined;
}

export function savedSegmentToRichSegment(
	segment: SavedSegment,
	category?: Pick<SegmentCategory, 'color'> | null
): RichSegment {
	return {
		type: segment.type,
		content: segment.content,
		chips: cloneChips(segment.chips),
		enabled: segment.enabled,
		name: segment.name,
		...(effectiveSavedSegmentColor(segment, category) ? { color: effectiveSavedSegmentColor(segment, category) } : {}),
		...(segment.description ? { description: segment.description } : {})
	};
}

/** Replace exactly one card with a detached Saved Segment copy. */
export function replaceFromSavedSegment(
	target: readonly Segment[],
	targetId: string,
	savedSegment: SavedSegment,
	category?: Pick<SegmentCategory, 'color'> | null,
	idFactory: SegmentIdFactory = createEditorSegmentId
): Segment[] {
	let replaced = false;
	const result = target.map((segment) => {
		if (segment.id !== targetId || replaced) return segment;
		replaced = true;
		return toEditorSegment(savedSegmentToRichSegment(savedSegment, category), idFactory);
	});
	return ensureSegmentList(result, idFactory);
}

export function removeSegmentKeepingOne(target: readonly Segment[], segmentId: string): Segment[] {
	if (target.length <= 1) return [...target];
	return target.filter((segment) => segment.id !== segmentId);
}

/** Preset-declared separator between enabled content segments (`vars.prompt.segment_join`
 *  in preset.yml). `comma` is the default every existing preset gets; `paragraph` is for
 *  presets whose segments are prose blocks (e.g. song sections) rather than tag fragments. */
export type SegmentJoin = 'comma' | 'paragraph';

/** Resolve enabled rich segments to the exact flattened text used for search and generation. */
export function flattenRichSegments(
	segments: readonly (Segment | RichSegment)[] = [],
	join: SegmentJoin = 'comma'
): string {
	let result = '';
	let previousWasBreak = false;

	for (const segment of segments) {
		// Intentionally ignore legacy `isCollapsed`: collapse is presentation only.
		if (!isSegmentEnabled(segment)) continue;

		if (segment.type === 'break') {
			result += result ? ' BREAK' : 'BREAK';
			previousWasBreak = true;
			continue;
		}

		const chips = segment.chips || {};
		const resolved = Object.keys(chips).length
			? richTextToPlainText(segment.content || '', chips)
			: segment.content || '';
		const trimmed = resolved.trim();
		if (!trimmed) continue;

		if (result) result += previousWasBreak ? ' ' : join === 'paragraph' ? '\n\n' : ', ';
		result += trimmed;
		previousWasBreak = false;
	}

	return result.trim();
}
