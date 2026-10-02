import { itemLimitFor, type MediaKind, type MediaLoaderLimits } from './mediaLoaderConfig';
import {
	renderResourceToken,
	resourceHandleLabel,
	type PromptResourceSpec
} from '$lib/utils/promptResources';
import { groupIntoLanes, moveWithinLane } from './mediaLoaderReorder';

export interface GroupedItem<T> {
	item: T;
	flatIndex: number;
	kindIndex: number;
	position: number;
}

export interface MediaGroup<T> {
	kind: MediaKind;
	items: GroupedItem<T>[];
	limit: number | null;
	count: number;
	full: boolean;
}

export interface GroupLayout<T> {
	eyebrows: boolean;
	visible: MediaGroup<T>[];
	folded: MediaGroup<T>[];
}

export function buildMediaGroups<T>(
	items: readonly T[],
	kinds: readonly MediaKind[],
	limits: Pick<MediaLoaderLimits, 'maxItems' | 'maxItemsByKind'>,
	kindOf: (item: T) => MediaKind | null
): MediaGroup<T>[] {
	return groupIntoLanes(items, kinds, kindOf).map((lane) => {
		const limit = itemLimitFor(limits, lane.kind);
		return {
			kind: lane.kind,
			items: lane.items.map((item, kindIndex) => ({
				item,
				flatIndex: lane.indices[kindIndex],
				kindIndex,
				position: kindIndex + 1
			})),
			limit,
			count: lane.items.length,
			full: limit !== null && lane.items.length >= limit
		};
	});
}

export function layoutGroups<T>(
	groups: MediaGroup<T>[],
	adding: MediaKind | null = null,
	fold: boolean = true
): GroupLayout<T> {
	if (groups.length <= 1) return { eyebrows: false, visible: groups, folded: [] };
	if (!fold) return { eyebrows: true, visible: groups, folded: [] };
	return {
		eyebrows: true,
		visible: groups.filter((group) => group.count > 0 || group.kind === adding),
		folded: groups.filter((group) => group.count === 0 && !group.full && group.kind !== adding)
	};
}

export function displayOrder<T>(groups: readonly MediaGroup<T>[]): number[] {
	return groups.flatMap((group) => group.items.map((entry) => entry.flatIndex));
}

export function clampSelection(selected: number | null, count: number): number | null {
	if (count === 0) return null;
	if (selected === null || selected < 0) return 0;
	return Math.min(selected, count - 1);
}

export function moveSelection<T>(
	groups: readonly MediaGroup<T>[],
	selected: number | null,
	direction: -1 | 1
): number | null {
	const order = displayOrder(groups);
	if (order.length === 0) return null;
	const at = selected === null ? -1 : order.indexOf(selected);
	if (at === -1) return order[0];
	const next = at + direction;
	return next < 0 || next >= order.length ? selected : order[next];
}

export function nudgeInGroup<T>(
	items: readonly T[],
	group: MediaGroup<T>,
	kindIndex: number,
	direction: -1 | 1
): { items: T[]; flatIndex: number } | null {
	const target = kindIndex + direction;
	if (target < 0 || target >= group.items.length) return null;
	const indices = group.items.map((entry) => entry.flatIndex);
	return {
		items: moveWithinLane(items, indices, kindIndex, target),
		flatIndex: indices[target]
	};
}

export function dropInGroup<T>(
	items: readonly T[],
	group: MediaGroup<T>,
	from: number,
	to: number
): { items: T[]; flatIndex: number } {
	const indices = group.items.map((entry) => entry.flatIndex);
	return {
		items: moveWithinLane(items, indices, from, to),
		flatIndex: indices[Math.max(0, Math.min(indices.length - 1, to))]
	};
}

const HANDLE_PREFIX: Record<MediaKind, { long: string; short: string }> = {
	image: { long: 'Picture', short: 'P' },
	video: { long: 'Video', short: 'V' },
	audio: { long: 'Audio', short: 'A' }
};

export function handleText(kind: MediaKind, position: number, short: boolean): string {
	const prefix = HANDLE_PREFIX[kind];
	return short ? `${prefix.short}${position}` : `${prefix.long} ${position}`;
}

export const GROUP_TITLES: Record<MediaKind, string> = {
	image: 'Images',
	video: 'Videos',
	audio: 'Audio'
};

export const ADD_LABELS: Record<MediaKind, string> = {
	image: 'Add image',
	video: 'Add video',
	audio: 'Add audio'
};

export function formatCount(count: number, limit: number | null): string {
	return limit === null ? String(count) : `${count}/${limit}`;
}

export interface ItemHandle {
	long: string;
	short: string;
	uses: number;
	tooltip: string;
}

export function describeHandle(
	kind: MediaKind,
	position: number,
	spec: PromptResourceSpec | null,
	uses: number
): ItemHandle {
	const tooltip = spec
		? `${renderResourceToken(spec, position)} in the prompt, ${
				uses ? `used ${uses} ${uses === 1 ? 'time' : 'times'}` : 'not used yet'
			}`
		: '';
	return {
		long: spec ? resourceHandleLabel(spec, position) : handleText(kind, position, false),
		short: handleText(kind, position, true),
		uses,
		tooltip
	};
}
