import type { MediaRef, ToolTabHint } from '$lib/types/tabs';
import { lastToolFor } from './lastTool';
import type { EditorKind } from './toolIds';

export const EDIT_ACTION = 'edit-image';
export const LOOK_ACTION = 'apply-look';
export const LOOK_PREFIX = 'look:';

export interface ToolShortcut {
	tool: EditorKind;
	activeTool: string;
	hint?: ToolTabHint;
}

export interface ShortcutItem {
	id: string;
	kind: string;
	url: string;
	filename: string;
	itemId?: string | null;
	generationFileId?: string | null;
}

export interface ShortcutGroup<T extends { kind: string }> extends ToolShortcut {
	items: T[];
}

type Resolver = (last: (kind: EditorKind) => string) => Omit<ToolShortcut, 'tool'>;

const fixed = (activeTool: string, hint?: ToolTabHint): Resolver => () => (hint ? { activeTool, hint } : { activeTool });

const IMAGE_ACTIONS: Record<string, Resolver> = {
	crop: fixed('crop'),
	resize: fixed('resize'),
	rotate: fixed('crop', { orientation: 'rotate' }),
	flip: fixed('crop', { orientation: 'flip' }),
	fit_canvas: fixed('canvas'),
	color_key: fixed('colorKey'),
	crop_subject: fixed('subject'),
	[EDIT_ACTION]: (last) => ({ activeTool: last('image') }),
	[LOOK_ACTION]: fixed('effects')
};

const VIDEO_ACTIONS: Record<string, Resolver> = {
	trim: fixed('trim'),
	split: fixed('split'),
	crop: fixed('crop'),
	resize: fixed('resize'),
	speed: fixed('speed'),
	rotate: fixed('crop', { orientation: 'rotate' }),
	flip: fixed('crop', { orientation: 'flip' }),
	volume: fixed('volume'),
	mute: fixed('mute'),
	replace_audio: fixed('replace'),
	film_grain: fixed('effects', { effect: 'film_grain' }),
	fix_colors: fixed('effects', { effect: 'fix_colors' }),
	frame: fixed('frame'),
	[EDIT_ACTION]: (last) => ({ activeTool: last('video') })
};

export function shortcutFor(
	actionId: string,
	kind: string,
	last: (kind: EditorKind) => string = lastToolFor
): ToolShortcut | null {
	if (kind === 'image') {
		if (actionId.startsWith(LOOK_PREFIX)) {
			return { tool: 'image', activeTool: 'effects', hint: { look: actionId.slice(LOOK_PREFIX.length) } };
		}
		const resolver = IMAGE_ACTIONS[actionId];
		return resolver ? { tool: 'image', ...resolver(last) } : null;
	}
	if (kind === 'video') {
		const resolver = VIDEO_ACTIONS[actionId];
		return resolver ? { tool: 'video', ...resolver(last) } : null;
	}
	return null;
}

export function planShortcut<T extends { kind: string }>(
	actionId: string,
	items: readonly T[],
	last: (kind: EditorKind) => string = lastToolFor
): ShortcutGroup<T>[] | null {
	if (items.length === 0) return null;
	const groups: ShortcutGroup<T>[] = [];
	for (const item of items) {
		const shortcut = shortcutFor(actionId, item.kind, last);
		if (!shortcut) return null;
		const group = groups.find((candidate) => candidate.tool === shortcut.tool);
		if (group) group.items.push(item);
		else groups.push({ ...shortcut, items: [item] });
	}
	return groups;
}

export function shortcutTargets(actionId: string, items: readonly { kind: string }[]): EditorKind[] | null {
	const plan = planShortcut(actionId, items);
	return plan ? plan.map((group) => group.tool) : null;
}

const LABELS: Record<string, string> = { frame: 'Open frame in Image editor', volume: 'Audio' };

const FOLDED_INTO_AUDIO = new Set(['mute', 'replace_audio']);

export function shortcutLabel(actionId: string, items: readonly { kind: string }[]): string | null {
	return LABELS[actionId] && planShortcut(actionId, items) ? LABELS[actionId] : null;
}

export function isFoldedShortcut(actionId: string, items: readonly { kind: string }[]): boolean {
	return FOLDED_INTO_AUDIO.has(actionId) && planShortcut(actionId, items) !== null;
}

export function tabFooter(targets: readonly EditorKind[], count: number): string {
	const many = count > 1;
	if (targets.length > 1) {
		return many
			? 'Each opens one tab per item in the Image or Video editor, with that tool ready.'
			: 'Each opens this item in the Image or Video editor, with that tool ready.';
	}
	if (targets[0] === 'video') {
		return many
			? 'Each opens one Video editor tab per clip, with that tool ready.'
			: 'Each opens this clip in a Video editor tab, with that tool ready.';
	}
	return many
		? 'Each opens one Image editor tab per picture, with that tool ready.'
		: 'Each opens this picture in an Image editor tab, with that tool ready.';
}

export function tabLabel(target: EditorKind): string {
	return target === 'video' ? 'Video editor' : 'Image editor';
}

export function mediaRefForItem(item: ShortcutItem): MediaRef {
	return {
		path: item.url,
		url: item.url,
		name: item.filename,
		type: item.kind,
		...(item.itemId ? { item_id: item.itemId } : {}),
		...(item.generationFileId ? { generation_file_id: item.generationFileId } : {})
	};
}

export function uniqueTargets(lists: readonly (readonly EditorKind[] | null | undefined)[]): EditorKind[] {
	const seen = new Set<EditorKind>();
	for (const list of lists) for (const target of list ?? []) seen.add(target);
	return [...seen];
}
