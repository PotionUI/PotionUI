export type FrameAction = 'undo' | 'redo' | 'compare' | 'save' | 'apply' | 'pan';

export interface FrameKey {
	key: string;
	ctrlKey: boolean;
	metaKey: boolean;
	shiftKey: boolean;
	altKey: boolean;
	repeat?: boolean;
}

export const FRAME_SHORTCUTS: ReadonlyArray<{ action: FrameAction; keys: string }> = [
	{ action: 'undo', keys: 'Ctrl Z' },
	{ action: 'redo', keys: 'Ctrl Shift Z' },
	{ action: 'compare', keys: '\\' },
	{ action: 'save', keys: 'Ctrl S' },
	{ action: 'apply', keys: 'Enter' },
	{ action: 'pan', keys: 'Space' }
];

export function shortcutFor(action: FrameAction): string {
	return FRAME_SHORTCUTS.find((entry) => entry.action === action)?.keys ?? '';
}

export function resolveFrameKey(event: FrameKey): FrameAction | null {
	if (event.altKey) return null;
	const mod = event.ctrlKey || event.metaKey;
	const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
	if (mod) {
		if (key === 'z') return event.shiftKey ? 'redo' : 'undo';
		if (key === 's' && !event.shiftKey) return 'save';
		return null;
	}
	if (event.shiftKey) return null;
	if (key === '\\') return event.repeat ? null : 'compare';
	if (key === 'Enter') return event.repeat ? null : 'apply';
	if (key === ' ') return 'pan';
	return null;
}

const ACTIVATES_ON_KEY = 'button, a[href], summary, select, input[type="checkbox"], input[type="radio"], input[type="button"], input[type="submit"], input[type="reset"], input[type="file"], input[type="color"], [role="button"], [role="menuitem"], [role="tab"], [role="option"], [role="switch"], [role="checkbox"], [role="radio"]';

export function targetActivatesOn(target: EventTarget | null): boolean {
	return typeof Element !== 'undefined' && target instanceof Element && target.closest(ACTIVATES_ON_KEY) !== null;
}
