import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { Segment } from '$lib/types/segments';
import {
	deriveResourcesFromText,
	encodeResourceMarker,
	findResourceSpec,
	kindLabel,
	mediaFieldItems,
	mediaItemKey,
	resourceHandleLabel,
	type PromptResourceKind,
	type PromptResourceSpec
} from './promptResources';
import { kindOfMediaItem } from '$lib/components/form-fields/mediaLoaderKind';
import {
	citeableRefModRows,
	refModBadge,
	refModCopies,
	refModFields,
	refModId,
	refModMemberKinds,
	refModName,
	refModPrimaryKind,
	type RefModDirectory
} from './refMods';
import { countResourceReferences, resourceUseCount, type UsageSegment } from './promptResourceUsage';
import { resolvePromptSegments } from './promptSegments';
import type { ResourceNumbering } from './resourceNumbering';

export interface ShotReferenceEntry {
	field: string;
	itemKey: string;
	kind: PromptResourceKind;
	name: string;
	url: string | null;
	count: number;
	handle: string | null;
	marker: string;
	badge: string | null;
}

export interface ShotReferenceOverview {
	used: ShotReferenceEntry[];
	unused: ShotReferenceEntry[];
}

function itemSpec(
	specs: readonly PromptResourceSpec[],
	field: string,
	item: unknown
): { kind: PromptResourceKind; spec: PromptResourceSpec | undefined } {
	const fieldSpecs = specs.filter((spec) => spec.field === field);
	if (fieldSpecs.length === 1) return { kind: fieldSpecs[0].kind, spec: fieldSpecs[0] };
	const kind = kindOfMediaItem(item) ?? 'image';
	return { kind, spec: findResourceSpec(specs, field, kind) };
}

function itemName(item: unknown, key: string): string {
	if (item && typeof item === 'object') {
		const record = item as Record<string, unknown>;
		for (const field of ['label', 'name'] as const) {
			const value = record[field];
			if (typeof value === 'string' && value.trim()) return value.trim();
		}
	}
	return key.replace(/\\/g, '/').split('/').pop() || key;
}

function itemUrl(item: unknown): string | null {
	if (item && typeof item === 'object') {
		const url = (item as Record<string, unknown>).url;
		if (typeof url === 'string' && url) return url;
	}
	return null;
}

export function shotPromptSegments(doc: VideoDirectorValue, caps: DirectorCapabilities, shotId: string): Segment[] {
	if (caps.segmentRouting) {
		return doc.chain.segments.find((s) => s.id === shotId)?.prompt_segments ?? [];
	}
	return doc.timeline.shots.find((s) => s.id === shotId)?.segments.flatMap((s) => s.prompt_segments) ?? [];
}

export function shotReferenceSegmentGroups(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string
): UsageSegment[][] {
	return [shotPromptSegments(doc, caps, shotId), doc.global_prompt_segments ?? [], doc.negative_prompt_segments ?? []];
}

function resourceIdentity(field: string, itemKey: string): string {
	return `${field}\u0000${itemKey}`;
}

export function shotResourcePositions(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData: Record<string, unknown> | null | undefined,
	specs: readonly PromptResourceSpec[],
	include: string | null = null
): Map<string, number> {
	const counts = countResourceReferences(shotReferenceSegmentGroups(doc, caps, shotId));
	const positions = new Map<string, number>();
	const perKind: Partial<Record<PromptResourceKind, number>> = {};
	const seen = new Set<string>();
	const refMods = refModFields(specs);
	for (const field of caps.referenceFields) {
		if (refMods.has(field)) continue;
		for (const item of mediaFieldItems(formData?.[field])) {
			const itemKey = mediaItemKey(item);
			if (!itemKey) continue;
			const identity = resourceIdentity(field, itemKey);
			if (seen.has(identity)) continue;
			seen.add(identity);
			if (identity !== include && resourceUseCount(counts, field, itemKey) <= 0) continue;
			const { kind } = itemSpec(specs, field, item);
			const position = (perKind[kind] ?? 0) + 1;
			perKind[kind] = position;
			positions.set(identity, position);
		}
	}
	return positions;
}

export function shotResourceNumbering(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData: Record<string, unknown> | null | undefined,
	specs: readonly PromptResourceSpec[]
): ResourceNumbering {
	const positions = shotResourcePositions(doc, caps, shotId, formData, specs);
	return {
		positionFor: (field, itemKey) => positions.get(resourceIdentity(field, itemKey)) ?? null,
		positionIfAdded: (field, itemKey) => {
			const identity = resourceIdentity(field, itemKey);
			const existing = positions.get(identity);
			if (existing !== undefined) return existing;
			return shotResourcePositions(doc, caps, shotId, formData, specs, identity).get(identity) ?? null;
		}
	};
}

export interface ShotReferenceContext {
	specs: readonly PromptResourceSpec[];
	directory: RefModDirectory;
}

export interface ShotReferenceUnit {
	field: string;
	itemKey: string;
	kind: PromptResourceKind;
}

export interface ShotReferenceCounts {
	used: number;
	total: number;
}

const KIND_RANK: readonly PromptResourceKind[] = ['image', 'video', 'audio'];

export function shotReferencePool(
	caps: DirectorCapabilities,
	formData: Record<string, unknown> | null | undefined,
	context: ShotReferenceContext
): ShotReferenceUnit[] {
	const refMods = refModFields(context.specs);
	const natives: ShotReferenceUnit[] = [];
	const mods: ShotReferenceUnit[] = [];
	for (const field of caps.referenceFields) {
		if (refMods.has(field)) {
			for (const row of citeableRefModRows(formData?.[field])) {
				const kinds = refModMemberKinds(context.directory[refModId(row.model)]);
				for (let copy = 0; copy < refModCopies(row); copy++) {
					for (const kind of kinds) mods.push({ field, itemKey: row.model, kind });
				}
			}
			continue;
		}
		for (const item of mediaFieldItems(formData?.[field])) {
			const itemKey = mediaItemKey(item);
			if (itemKey) natives.push({ field, itemKey, kind: itemSpec(context.specs, field, item).kind });
		}
	}
	return [...natives, ...mods].sort((a, b) => KIND_RANK.indexOf(a.kind) - KIND_RANK.indexOf(b.kind));
}

function citedUnits(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	pool: readonly ShotReferenceUnit[]
): ShotReferenceUnit[] {
	const counts = countResourceReferences(shotReferenceSegmentGroups(doc, caps, shotId));
	return pool.filter((unit) => resourceUseCount(counts, unit.field, unit.itemKey) > 0);
}

export function shotReferenceCounts(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData: Record<string, unknown> | null | undefined,
	context: ShotReferenceContext
): ShotReferenceCounts {
	const pool = shotReferencePool(caps, formData, context);
	return { used: citedUnits(doc, caps, shotId, pool).length, total: pool.length };
}

function refModHandles(cited: readonly ShotReferenceUnit[], refModFieldNames: ReadonlySet<string>): Map<string, string> {
	const perKind: Partial<Record<PromptResourceKind, number>> = {};
	const labels = new Map<string, string[]>();
	for (const unit of cited) {
		const position = (perKind[unit.kind] ?? 0) + 1;
		perKind[unit.kind] = position;
		if (!refModFieldNames.has(unit.field)) continue;
		const identity = resourceIdentity(unit.field, unit.itemKey);
		labels.set(identity, [...(labels.get(identity) ?? []), `${kindLabel(unit.kind)} ${position}`]);
	}
	return new Map([...labels].map(([identity, list]) => [identity, list.join(' · ')]));
}

export function shotReferenceOverview(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData: Record<string, unknown> | null | undefined,
	specs: readonly PromptResourceSpec[],
	refModDirectory: RefModDirectory = {}
): ShotReferenceOverview {
	const counts = countResourceReferences(shotReferenceSegmentGroups(doc, caps, shotId));
	const positions = shotResourcePositions(doc, caps, shotId, formData, specs);
	const used: ShotReferenceEntry[] = [];
	const unused: ShotReferenceEntry[] = [];
	const seen = new Set<string>();
	const refMods = refModFields(specs);
	const context = { specs, directory: refModDirectory };
	const handles = refModHandles(citedUnits(doc, caps, shotId, shotReferencePool(caps, formData, context)), refMods);
	for (const field of caps.referenceFields) {
		const isRefMod = refMods.has(field);
		for (const item of isRefMod ? citeableRefModRows(formData?.[field]) : mediaFieldItems(formData?.[field])) {
			const itemKey = mediaItemKey(item);
			if (!itemKey) continue;
			const identity = resourceIdentity(field, itemKey);
			if (seen.has(identity)) continue;
			seen.add(identity);
			if (isRefMod) {
				const info = refModDirectory[refModId(itemKey)];
				const refModEntry: ShotReferenceEntry = {
					field,
					itemKey,
					kind: refModPrimaryKind(info),
					name: refModName(itemKey, refModDirectory),
					url: null,
					count: resourceUseCount(counts, field, itemKey),
					handle: handles.get(identity) ?? null,
					marker: encodeResourceMarker(field, itemKey),
					badge: refModBadge(info)
				};
				(refModEntry.count > 0 ? used : unused).push(refModEntry);
				continue;
			}
			const { kind, spec } = itemSpec(specs, field, item);
			const count = resourceUseCount(counts, field, itemKey);
			const position = positions.get(identity) ?? null;
			const entry: ShotReferenceEntry = {
				field,
				itemKey,
				kind,
				name: itemName(item, itemKey),
				url: itemUrl(item),
				count,
				handle: position !== null && spec ? resourceHandleLabel(spec, position) : null,
				marker: encodeResourceMarker(field, itemKey),
				badge: null
			};
			if (count > 0) {
				used.push(entry);
			} else {
				unused.push(entry);
			}
		}
	}
	return { used, unused };
}

export const NO_REFERENCES_HINT = 'This shot uses no references. Type @ to add one.';

export function shotNeedsReferenceHint(overview: ShotReferenceOverview): boolean {
	return overview.used.length === 0 && overview.unused.length > 0;
}

export function withMarkerAppendedToShot(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	marker: string
): VideoDirectorValue {
	const append = (segments: Segment[], fallbackId: string): Segment[] => {
		const index = segments.findIndex((s) => s.enabled !== false && s.isDisabled !== true);
		const target: Segment = index === -1 ? { id: segments.length ? `${fallbackId}-${segments.length}` : fallbackId, content: '' } : segments[index];
		const content = target.content ? `${target.content.replace(/\s+$/, '')} ${marker}` : marker;
		const next: Segment = { ...target, content, resources: deriveResourcesFromText(content, target.resources ?? {}) };
		return index === -1 ? [...segments, next] : segments.map((s, i) => (i === index ? next : s));
	};
	if (caps.segmentRouting) {
		return {
			...doc,
			chain: {
				...doc.chain,
				segments: doc.chain.segments.map((s) => {
					if (s.id !== shotId) return s;
					const prompt_segments = append(s.prompt_segments, `${s.id}-prompt-0`);
					return { ...s, prompt_segments, prompt: resolvePromptSegments(prompt_segments) };
				})
			}
		};
	}
	return {
		...doc,
		timeline: {
			...doc.timeline,
			shots: doc.timeline.shots.map((shot) => {
				if (shot.id !== shotId || shot.segments.length === 0) return shot;
				const [beat, ...beats] = shot.segments;
				const prompt_segments = append(beat.prompt_segments, `${beat.id}-prompt-0`);
				return { ...shot, segments: [{ ...beat, prompt_segments, text: resolvePromptSegments(prompt_segments) }, ...beats] };
			})
		}
	};
}
