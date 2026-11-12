import type { PickerMediaType } from '../mediaPickerSelection';
import type { PickerSourceId } from './pickerModel';
import { KIND_LABELS } from './pickerModel';

export const KIT_ITEM_PREFIX = 'kit:';
export const WHOLE_KIT_PREFIX = 'kit:all:';

export function kitItemKey(itemId: string): string {
	return `${KIT_ITEM_PREFIX}${itemId}`;
}

export function wholeKitKey(kitId: string): string {
	return `${WHOLE_KIT_PREFIX}${kitId}`;
}

export function isWholeKitKey(key: string): boolean {
	return key.startsWith(WHOLE_KIT_PREFIX);
}

export function kitIdOfWholeKey(key: string): string {
	return key.slice(WHOLE_KIT_PREFIX.length);
}

export function sourceOfKey(key: string): PickerSourceId {
	if (key.startsWith(KIT_ITEM_PREFIX)) return 'kits';
	return key.includes(':') ? 'history' : 'library';
}

export function selectedBySource(keys: readonly string[]): Record<PickerSourceId, number> {
	const counts: Record<PickerSourceId, number> = { history: 0, library: 0, kits: 0 };
	for (const key of keys) counts[sourceOfKey(key)] += 1;
	return counts;
}

export function splitKeys(keys: readonly string[]): { itemKeys: string[]; kitIds: string[] } {
	const itemKeys: string[] = [];
	const kitIds: string[] = [];
	for (const key of keys) {
		if (isWholeKitKey(key)) kitIds.push(kitIdOfWholeKey(key));
		else itemKeys.push(key);
	}
	return { itemKeys, kitIds };
}

export function usedCount(keys: readonly string[], kitFit: (kitId: string) => number): number {
	let used = 0;
	for (const key of keys) used += isWholeKitKey(key) ? kitFit(kitIdOfWholeKey(key)) : 1;
	return used;
}

export function roomLeft(max: number | null, used: number): number {
	return max === null ? Number.POSITIVE_INFINITY : Math.max(0, max - used);
}

export function isAtLimit(max: number | null, used: number): boolean {
	return max !== null && used >= max;
}

export function canAdd(max: number | null, used: number, adding: number): boolean {
	return max === null || used + adding <= max;
}

export function toggleWithLimit(
	keys: readonly string[],
	key: string,
	max: number | null,
	kitFit: (kitId: string) => number
): string[] {
	if (keys.includes(key)) return keys.filter((existing) => existing !== key);
	const adding = isWholeKitKey(key) ? kitFit(kitIdOfWholeKey(key)) : 1;
	if (adding === 0) return [...keys];
	if (!canAdd(max, usedCount(keys, kitFit), adding)) return [...keys];
	return [...keys, key];
}

export function kindsPhrase(kinds: readonly PickerMediaType[]): string {
	const names = kinds.map((kind) => (kind === 'audio' ? 'audio' : kind === 'mesh' ? '3D files' : `${kind}s`));
	if (names.length <= 1) return names[0] ?? 'media';
	return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function acceptedOnlyReason(kinds: readonly PickerMediaType[]): string {
	const phrase = kindsPhrase(kinds);
	return `${phrase.charAt(0).toUpperCase()}${phrase.slice(1)} only`;
}

export function noKindInKitReason(kinds: readonly PickerMediaType[]): string {
	return `No ${kindsPhrase(kinds)} in this kit`;
}

export function limitReason(max: number): string {
	return `Limit of ${max} reached`;
}

export interface TileDim {
	disabled: boolean;
	reason: string;
}

const ENABLED: TileDim = { disabled: false, reason: '' };

export function itemDim(input: {
	allowed: boolean;
	acceptedKinds: readonly PickerMediaType[];
	selected: boolean;
	multiple: boolean;
	max: number | null;
	used: number;
}): TileDim {
	if (!input.allowed) return { disabled: true, reason: acceptedOnlyReason(input.acceptedKinds) };
	if (input.multiple && !input.selected && input.max !== null && isAtLimit(input.max, input.used)) {
		return { disabled: true, reason: limitReason(input.max) };
	}
	return ENABLED;
}

export function kitCardDim(input: {
	fit: number;
	selected: boolean;
	multiple: boolean;
	max: number | null;
	used: number;
	acceptedKinds: readonly PickerMediaType[];
}): TileDim {
	if (input.fit === 0) return { disabled: true, reason: noKindInKitReason(input.acceptedKinds) };
	if (input.multiple && !input.selected && input.max !== null && !canAdd(input.max, input.used, input.fit)) {
		return { disabled: true, reason: limitReason(input.max) };
	}
	return ENABLED;
}

export function wholeKitLabel(fit: number, kinds: readonly PickerMediaType[]): string {
	const noun = kinds.length === 1 ? kinds[0] : 'item';
	return `${fit} ${noun}${fit === 1 ? '' : 's'}`;
}

export function wholeKitBlocked(fit: number, max: number | null, used: number): boolean {
	return fit === 0 || !canAdd(max, used, fit);
}

export function selectedCountText(count: number, max: number | null): string {
	return `${count}${max !== null ? ` of ${max}` : ''} selected`;
}

export function kindFilterItems(kinds: readonly PickerMediaType[]): Array<{ id: string; label: string }> {
	return [{ id: 'all', label: 'All' }, ...kinds.map((kind) => ({ id: kind, label: KIND_LABELS[kind] }))];
}

export type ArrowKey = 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown' | 'Home' | 'End';

export interface FocusRect {
	left: number;
	top: number;
	width: number;
	height: number;
}

export function nextFocusIndex(rects: readonly FocusRect[], current: number, key: ArrowKey): number {
	if (rects.length === 0) return -1;
	if (current < 0 || current >= rects.length) return 0;
	if (key === 'Home') return 0;
	if (key === 'End') return rects.length - 1;
	if (key === 'ArrowLeft') return Math.max(0, current - 1);
	if (key === 'ArrowRight') return Math.min(rects.length - 1, current + 1);

	const here = rects[current];
	const hereX = here.left + here.width / 2;
	const hereY = here.top + here.height / 2;
	const forward = key === 'ArrowDown';
	let best = -1;
	let bestRow = Number.POSITIVE_INFINITY;
	let bestDx = Number.POSITIVE_INFINITY;
	for (let i = 0; i < rects.length; i++) {
		if (i === current) continue;
		const centerY = rects[i].top + rects[i].height / 2;
		const delta = forward ? centerY - hereY : hereY - centerY;
		if (delta <= rects[current].height / 2) continue;
		const dx = Math.abs(rects[i].left + rects[i].width / 2 - hereX);
		if (delta < bestRow - 1 || (Math.abs(delta - bestRow) <= 1 && dx < bestDx)) {
			bestRow = delta;
			bestDx = dx;
			best = i;
		}
	}
	return best === -1 ? current : best;
}
