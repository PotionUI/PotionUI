import type { RichSegment, SegmentTemplate } from '$lib/types/segments';

/** Which library a Segment Template in the apply picker came from. */
export type SegmentTemplateOrigin = 'user' | 'preset';

/** A Segment Template declared by the selected preset's `vars.prompt`. It never
 *  exists in the database, so its `id` is synthesized here and is the only id
 *  anything downstream (provenance stamps included) may use for it. */
export interface PresetSegmentTemplate extends SegmentTemplate {
	origin: 'preset';
}

/**
 * Resolve the preset-declared Segment Templates that apply to `mode`.
 *
 * `vars.prompt.modes[mode].segment_templates` replaces the flat
 * `vars.prompt.segment_templates` outright when the key is present — an empty
 * list there means "this mode offers none", not "fall back".
 */
export function resolvePresetSegmentTemplates(
	presetId: string | null | undefined,
	promptVars: unknown,
	mode: string | null | undefined
): PresetSegmentTemplate[] {
	const prompt = asRecord(promptVars);
	if (!prompt) return [];

	const modeVars = mode ? asRecord(asRecord(prompt.modes)?.[mode]) : null;
	const overrides = !!modeVars && 'segment_templates' in modeVars;
	const source = overrides ? modeVars.segment_templates : prompt.segment_templates;
	if (!Array.isArray(source)) return [];

	const scope = overrides ? (mode as string) : '*';
	return source
		.map((entry, index) => normalizeEntry(entry, presetId || '', scope, index))
		.filter((template): template is PresetSegmentTemplate => template !== null);
}

function normalizeEntry(
	entry: unknown,
	presetId: string,
	scope: string,
	index: number
): PresetSegmentTemplate | null {
	const record = asRecord(entry);
	if (!record) return null;

	const name = typeof record.name === 'string' ? record.name.trim() : '';
	if (!name || !Array.isArray(record.segments)) return null;

	return {
		id: `preset:${presetId}:${scope}:${index}`,
		name,
		description: typeof record.description === 'string' ? record.description : null,
		tags: Array.isArray(record.tags) ? record.tags.filter((tag): tag is string => typeof tag === 'string') : [],
		segments: record.segments.filter((segment): segment is RichSegment => asRecord(segment) !== null),
		origin: 'preset'
	};
}

function asRecord(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}
