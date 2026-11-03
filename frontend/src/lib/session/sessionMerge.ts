import type { ModeBasedSessionData, SessionData } from '$lib/types/api';
import type { Segment } from '$lib/types/segments';

export type SegmentList = 'promptSegments' | 'negativePromptSegments';

export const SEGMENT_LISTS: readonly SegmentList[] = ['promptSegments', 'negativePromptSegments'];

export const WHOLE_FIELDS = [
	'selectedVariant',
	'seed',
	'promptTabs',
	'activePromptTab',
	'promptRelay',
	'videoDirector',
	'musicDirector'
] as const satisfies readonly (keyof SessionData)[];

const PROMPT_OF: Record<SegmentList, 'prompt' | 'negativePrompt'> = {
	promptSegments: 'prompt',
	negativePromptSegments: 'negativePrompt'
};

export interface SyncConflict {
	mode: string;
	key: string;
	local: unknown;
	remote: unknown;
}

export interface SyncChange {
	mode: string;
	key: string;
}

export interface SessionMergeResult {
	merged: ModeBasedSessionData;
	theirs: ModeBasedSessionData;
	applied: SyncChange[];
	conflicts: SyncConflict[];
}

export interface SessionMergeOptions {
	resolvePrompt?: (segments: Segment[], list: SegmentList) => string;
}

type Decision = 'keep' | 'theirs' | 'conflict';

export function sameValue(a: unknown, b: unknown): boolean {
	if (a === b) return true;
	if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
	if (Array.isArray(a) !== Array.isArray(b)) return false;
	if (Array.isArray(a) && Array.isArray(b)) {
		return a.length === b.length && a.every((item, index) => sameValue(item, b[index]));
	}
	const left = a as Record<string, unknown>;
	const right = b as Record<string, unknown>;
	const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
	for (const key of keys) {
		if (!sameValue(left[key], right[key])) return false;
	}
	return true;
}

function decide(base: unknown, local: unknown, remote: unknown, baseKnown: boolean): Decision {
	if (sameValue(local, remote)) return 'keep';
	if (baseKnown && sameValue(base, local)) return 'theirs';
	if (baseKnown && sameValue(base, remote)) return 'keep';
	return 'conflict';
}

export function segmentKey(list: SegmentList, id: string): string {
	return `seg:${list}:${id}`;
}

export function formKey(name: string): string {
	return `form:${name}`;
}

export function variableKey(name: string): string {
	return `var:${name}`;
}

function segmentsOf(data: SessionData | undefined, list: SegmentList): Segment[] | undefined {
	if (!data) return undefined;
	const legacy = list === 'promptSegments' ? data.segments : data.negativeSegments;
	return legacy || data[list];
}

function byId(segments: Segment[]): Map<string, Segment> {
	return new Map(segments.map((segment) => [segment.id, segment]));
}

function relativeOrderKept(base: string[], other: string[]): boolean {
	const baseCommon = base.filter((id) => other.includes(id));
	const otherCommon = other.filter((id) => base.includes(id));
	return sameValue(baseCommon, otherCommon);
}

function placeMissing(order: string[], wanted: Set<string>, source: string[]): void {
	for (let index = 0; index < source.length; index += 1) {
		const id = source[index];
		if (!wanted.has(id) || order.includes(id)) continue;
		let at = 0;
		for (let back = index - 1; back >= 0; back -= 1) {
			const position = order.indexOf(source[back]);
			if (position >= 0) {
				at = position + 1;
				break;
			}
		}
		order.splice(at, 0, id);
	}
}

interface ListMerge {
	merged: Segment[];
	theirs: Segment[];
}

function mergeSegments(
	mode: string,
	list: SegmentList,
	base: Segment[],
	local: Segment[],
	remote: Segment[],
	baseKnown: boolean,
	out: { applied: SyncChange[]; conflicts: SyncConflict[] }
): ListMerge {
	const b = byId(base);
	const l = byId(local);
	const r = byId(remote);
	const baseIds = base.map((segment) => segment.id);
	const localIds = local.map((segment) => segment.id);
	const remoteIds = remote.map((segment) => segment.id);
	const chosen = new Map<string, Segment>();
	for (const id of new Set([...localIds, ...remoteIds, ...baseIds])) {
		const key = segmentKey(list, id);
		const decision = decide(b.get(id), l.get(id), r.get(id), baseKnown);
		if (decision === 'theirs') {
			const remoteSegment = r.get(id);
			if (remoteSegment) {
				chosen.set(id, remoteSegment);
				out.applied.push({ mode, key });
			}
			continue;
		}
		if (decision === 'conflict') {
			out.conflicts.push({ mode, key, local: l.get(id), remote: r.get(id) });
			const shown = l.get(id) ?? r.get(id);
			if (shown) chosen.set(id, shown);
			continue;
		}
		const kept = l.get(id);
		if (kept) chosen.set(id, kept);
	}
	const spine = baseKnown && relativeOrderKept(baseIds, localIds) ? remoteIds : localIds;
	const wanted = new Set(chosen.keys());
	const order = spine.filter((id) => wanted.has(id));
	placeMissing(order, wanted, localIds);
	placeMissing(order, wanted, remoteIds);
	return { merged: order.map((id) => chosen.get(id)!), theirs: remote };
}

function mergeFormData(
	mode: string,
	base: Record<string, unknown> | undefined,
	local: Record<string, unknown> | undefined,
	remote: Record<string, unknown> | undefined,
	baseKnown: boolean,
	out: { applied: SyncChange[]; conflicts: SyncConflict[] }
): { merged: Record<string, unknown> | undefined; theirs: Record<string, unknown> | undefined } {
	if (!remote) return { merged: local, theirs: local };
	const merged: Record<string, unknown> = { ...(local ?? {}) };
	const theirs: Record<string, unknown> = { ...(local ?? {}) };
	for (const name of Object.keys(remote)) {
		const key = formKey(name);
		const decision = decide(base?.[name], local?.[name], remote[name], baseKnown);
		theirs[name] = remote[name];
		if (decision === 'theirs') {
			merged[name] = remote[name];
			out.applied.push({ mode, key });
		} else if (decision === 'conflict') {
			out.conflicts.push({ mode, key, local: local?.[name], remote: remote[name] });
		}
	}
	for (const name of Object.keys(merged)) {
		if (!(name in remote) && base && name in base) theirs[name] = base[name];
	}
	return { merged, theirs };
}

function mergeVariables(
	mode: string,
	base: Record<string, unknown>,
	local: Record<string, unknown>,
	remote: Record<string, unknown>,
	baseKnown: boolean,
	out: { applied: SyncChange[]; conflicts: SyncConflict[] }
): Record<string, unknown> {
	const merged: Record<string, unknown> = {};
	for (const name of new Set([...Object.keys(local), ...Object.keys(remote), ...Object.keys(base)])) {
		const key = variableKey(name);
		const decision = decide(base[name], local[name], remote[name], baseKnown);
		if (decision === 'theirs') {
			if (remote[name] !== undefined) merged[name] = remote[name];
			out.applied.push({ mode, key });
		} else if (decision === 'conflict') {
			out.conflicts.push({ mode, key, local: local[name], remote: remote[name] });
			const shown = local[name] ?? remote[name];
			if (shown !== undefined) merged[name] = shown;
		} else if (local[name] !== undefined) {
			merged[name] = local[name];
		}
	}
	return merged;
}

function withVariables(entry: SessionData, variables: Record<string, unknown>): SessionData {
	const { variables: _previous, ...rest } = entry;
	return Object.keys(variables).length > 0 ? { ...rest, variables: variables as SessionData['variables'] } : rest;
}

function withFormData(entry: SessionData, formData: Record<string, unknown> | undefined): SessionData {
	const { formData: _previous, ...rest } = entry;
	return formData === undefined ? rest : { ...rest, formData };
}

export function mergeModeData(
	mode: string,
	base: SessionData | undefined,
	local: SessionData | undefined,
	remote: SessionData | undefined,
	baseKnown: boolean,
	options: SessionMergeOptions = {}
): { merged: SessionData; theirs: SessionData; applied: SyncChange[]; conflicts: SyncConflict[] } {
	const out = { applied: [] as SyncChange[], conflicts: [] as SyncConflict[] };
	const localEntry = local ?? {};
	if (!remote) return { merged: localEntry, theirs: localEntry, ...out };
	let merged: SessionData = { ...localEntry };
	let theirs: SessionData = { ...localEntry };

	for (const field of WHOLE_FIELDS) {
		if (remote[field] === undefined) {
			(theirs as Record<string, unknown>)[field] = base?.[field] ?? localEntry[field];
			continue;
		}
		const decision = decide(base?.[field], localEntry[field], remote[field], baseKnown);
		(theirs as Record<string, unknown>)[field] = remote[field];
		if (decision === 'theirs') {
			(merged as Record<string, unknown>)[field] = remote[field];
			out.applied.push({ mode, key: field });
		} else if (decision === 'conflict') {
			out.conflicts.push({ mode, key: field, local: localEntry[field], remote: remote[field] });
		}
	}

	for (const list of SEGMENT_LISTS) {
		const promptField = PROMPT_OF[list];
		const baseSegments = segmentsOf(base, list) ?? [];
		const localSegments = segmentsOf(localEntry, list) ?? [];
		const remoteList = segmentsOf(remote, list);
		const remoteSegments = remoteList ?? [];
		const segmented = baseSegments.length > 0 || localSegments.length > 0 || remoteSegments.length > 0;
		if (!segmented) {
			if (remote[promptField] === undefined) continue;
			const decision = decide(base?.[promptField], localEntry[promptField], remote[promptField], baseKnown);
			theirs[promptField] = remote[promptField];
			if (decision === 'theirs') {
				merged[promptField] = remote[promptField];
				out.applied.push({ mode, key: promptField });
			} else if (decision === 'conflict') {
				out.conflicts.push({ mode, key: promptField, local: localEntry[promptField], remote: remote[promptField] });
			}
			continue;
		}
		if (remoteList === undefined) {
			theirs[list] = baseKnown ? baseSegments : localSegments;
			theirs[promptField] = baseKnown ? base?.[promptField] : localEntry[promptField];
			continue;
		}
		const result = mergeSegments(mode, list, baseSegments, localSegments, remoteSegments, baseKnown, out);
		merged[list] = result.merged;
		theirs[list] = result.theirs;
		theirs[promptField] = remote[promptField] ?? options.resolvePrompt?.(remoteSegments, list) ?? '';
		if (sameValue(result.merged, localSegments)) continue;
		merged[promptField] = sameValue(result.merged, remoteSegments)
			? theirs[promptField]
			: (options.resolvePrompt?.(result.merged, list) ?? localEntry[promptField]);
	}

	const form = mergeFormData(mode, base?.formData, localEntry.formData, remote.formData, baseKnown, out);
	merged = withFormData(merged, form.merged);
	theirs = withFormData(theirs, form.theirs);

	const variables = mergeVariables(
		mode,
		(base?.variables ?? {}) as Record<string, unknown>,
		(localEntry.variables ?? {}) as Record<string, unknown>,
		(remote.variables ?? {}) as Record<string, unknown>,
		baseKnown,
		out
	);
	merged = withVariables(merged, variables);
	theirs = withVariables(theirs, (remote.variables ?? {}) as Record<string, unknown>);

	return { merged, theirs, ...out };
}

export function mergeSessionData(
	base: ModeBasedSessionData | null,
	local: ModeBasedSessionData,
	remote: ModeBasedSessionData,
	options: SessionMergeOptions = {}
): SessionMergeResult {
	const baseKnown = base !== null;
	const merged: ModeBasedSessionData = { ...local };
	const theirs: ModeBasedSessionData = { ...local };
	const applied: SyncChange[] = [];
	const conflicts: SyncConflict[] = [];
	for (const mode of new Set([...Object.keys(local), ...Object.keys(remote)])) {
		const result = mergeModeData(mode, base?.[mode], local[mode], remote[mode], baseKnown, options);
		merged[mode] = result.merged;
		theirs[mode] = result.theirs;
		applied.push(...result.applied);
		conflicts.push(...result.conflicts);
	}
	return { merged, theirs, applied, conflicts };
}

function parseSegmentKey(key: string): { list: SegmentList; id: string } | null {
	if (!key.startsWith('seg:')) return null;
	const rest = key.slice(4);
	const split = rest.indexOf(':');
	const list = rest.slice(0, split) as SegmentList;
	if (!SEGMENT_LISTS.includes(list)) return null;
	return { list, id: rest.slice(split + 1) };
}

export function fieldValue(entry: SessionData | undefined, key: string): unknown {
	if (!entry) return undefined;
	const segment = parseSegmentKey(key);
	if (segment) return segmentsOf(entry, segment.list)?.find((item) => item.id === segment.id);
	if (key.startsWith('form:')) return entry.formData?.[key.slice(5)];
	if (key.startsWith('var:')) return (entry.variables as Record<string, unknown> | undefined)?.[key.slice(4)];
	return (entry as Record<string, unknown>)[key];
}

export function withFieldValue(
	entry: SessionData,
	key: string,
	value: unknown,
	options: SessionMergeOptions = {}
): SessionData {
	const segment = parseSegmentKey(key);
	if (segment) {
		const current = segmentsOf(entry, segment.list) ?? [];
		const exists = current.some((item) => item.id === segment.id);
		const next =
			value === undefined
				? current.filter((item) => item.id !== segment.id)
				: exists
					? current.map((item) => (item.id === segment.id ? (value as Segment) : item))
					: [...current, value as Segment];
		const prompt = options.resolvePrompt?.(next, segment.list) ?? entry[PROMPT_OF[segment.list]];
		return { ...entry, [segment.list]: next, [PROMPT_OF[segment.list]]: prompt };
	}
	if (key.startsWith('form:')) {
		const formData = { ...(entry.formData ?? {}) };
		if (value === undefined) delete formData[key.slice(5)];
		else formData[key.slice(5)] = value;
		return { ...entry, formData };
	}
	if (key.startsWith('var:')) {
		const variables = { ...((entry.variables ?? {}) as Record<string, unknown>) };
		if (value === undefined) delete variables[key.slice(4)];
		else variables[key.slice(4)] = value;
		return withVariables(entry, variables);
	}
	return { ...entry, [key]: value };
}

const FIELD_NAMES: Record<string, string> = {
	selectedVariant: 'Variant',
	seed: 'Seed',
	prompt: 'Prompt',
	negativePrompt: 'Negative prompt',
	promptTabs: 'Prompt tabs',
	activePromptTab: 'Active prompt tab',
	promptRelay: 'Prompt relay',
	videoDirector: 'Video Director',
	musicDirector: 'Music Director'
};

export function describeSyncField(key: string, value?: unknown): string {
	const segment = parseSegmentKey(key);
	if (segment) {
		const name = segment.list === 'promptSegments' ? 'Prompt segment' : 'Negative segment';
		const content = (value as Segment | undefined)?.content?.trim();
		return content ? `${name} "${content.length > 32 ? `${content.slice(0, 32)}…` : content}"` : name;
	}
	if (key.startsWith('form:')) return key.slice(5);
	if (key.startsWith('var:')) return `\${${key.slice(4)}}`;
	return FIELD_NAMES[key] ?? key;
}
