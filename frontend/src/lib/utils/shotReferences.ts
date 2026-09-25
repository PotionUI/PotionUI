import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { Segment } from '$lib/types/segments';
import {
	deriveResourcesFromText,
	encodeResourceMarker,
	findResourceSpec,
	mediaFieldItems,
	mediaItemKey,
	resourceHandleLabel,
	type PromptResourceKind,
	type PromptResourceSpec
} from './promptResources';
import { countResourceReferences, resourceUseCount, type UsageSegment } from './promptResourceUsage';
import { resolvePromptSegments } from './promptSegments';

export interface ShotReferenceEntry {
	field: string;
	itemKey: string;
	kind: PromptResourceKind;
	name: string;
	url: string | null;
	count: number;
	handle: string | null;
	marker: string;
}

export interface ShotReferenceOverview {
	used: ShotReferenceEntry[];
	unused: ShotReferenceEntry[];
}

function itemKind(item: unknown, spec: PromptResourceSpec | undefined): PromptResourceKind {
	if (spec) return spec.kind;
	const type = item && typeof item === 'object' ? (item as Record<string, unknown>).type : undefined;
	return type === 'video' || type === 'audio' ? type : 'image';
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

export function shotReferenceOverview(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	formData: Record<string, unknown> | null | undefined,
	specs: readonly PromptResourceSpec[]
): ShotReferenceOverview {
	const counts = countResourceReferences(shotReferenceSegmentGroups(doc, caps, shotId));
	const used: ShotReferenceEntry[] = [];
	const unused: ShotReferenceEntry[] = [];
	const perKind: Partial<Record<PromptResourceKind, number>> = {};
	const seen = new Set<string>();
	for (const field of caps.referenceFields) {
		const spec = findResourceSpec(specs, field);
		for (const item of mediaFieldItems(formData?.[field])) {
			const itemKey = mediaItemKey(item);
			if (!itemKey) continue;
			const identity = `${field}\u0000${itemKey}`;
			if (seen.has(identity)) continue;
			seen.add(identity);
			const kind = itemKind(item, spec);
			const count = resourceUseCount(counts, field, itemKey);
			const entry: ShotReferenceEntry = {
				field,
				itemKey,
				kind,
				name: itemName(item, itemKey),
				url: itemUrl(item),
				count,
				handle: null,
				marker: encodeResourceMarker(field, itemKey)
			};
			if (count > 0) {
				const position = (perKind[kind] ?? 0) + 1;
				perKind[kind] = position;
				entry.handle = spec ? resourceHandleLabel(spec, position) : null;
				used.push(entry);
			} else {
				unused.push(entry);
			}
		}
	}
	return { used, unused };
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
