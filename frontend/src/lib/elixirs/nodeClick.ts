export const OPT_OUT_SELECTOR = [
	'button',
	'a',
	'input',
	'select',
	'textarea',
	'label',
	'summary',
	'video',
	'audio',
	'[role="button"]',
	'[role="link"]',
	'[role="checkbox"]',
	'[role="switch"]',
	'[role="tab"]',
	'[role="menuitem"]',
	'[role="option"]',
	'[contenteditable]:not([contenteditable="false"])',
	'[data-no-open]'
].join(', ');

export interface ActivateEvent {
	defaultPrevented?: boolean;
	metaKey?: boolean;
	ctrlKey?: boolean;
	shiftKey?: boolean;
	altKey?: boolean;
	button?: number;
	key?: string;
	target?: unknown;
	preventDefault?: () => void;
}

interface ClosestTarget {
	closest: (selector: string) => unknown;
	dataset?: Record<string, string | undefined>;
}

function plain(event: ActivateEvent | null | undefined): boolean {
	return !!event && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

export function shouldOpenSettings(event: ActivateEvent | null | undefined, selectedText: unknown = ''): boolean {
	if (!event || !plain(event)) return false;
	if (typeof event.button === 'number' && event.button !== 0) return false;
	if (String(selectedText ?? '').trim() !== '') return false;
	const target = event.target as ClosestTarget | null | undefined;
	if (!target || typeof target.closest !== 'function') return false;
	return target.closest(OPT_OUT_SELECTOR) === null;
}

export function pageSelection(): string {
	return typeof window !== 'undefined' && window.getSelection ? (window.getSelection()?.toString() ?? '') : '';
}

export function nodeIdForKey(event: ActivateEvent | null | undefined): string | null {
	if (!event || !plain(event) || event.key !== 'Enter') return null;
	const target = event.target as ClosestTarget | null | undefined;
	if (!target) return null;
	return target.dataset?.nodeRoot ?? null;
}

export function openOnActivate(element: HTMLElement, open: () => void) {
	let current = open;
	const onclick = (event: MouseEvent) => {
		if (shouldOpenSettings(event, pageSelection())) current();
	};
	const onkeydown = (event: KeyboardEvent) => {
		if (nodeIdForKey(event)) {
			event.preventDefault();
			current();
		}
	};
	element.addEventListener('click', onclick);
	element.addEventListener('keydown', onkeydown);
	return {
		update(next: () => void) {
			current = next;
		},
		destroy() {
			element.removeEventListener('click', onclick);
			element.removeEventListener('keydown', onkeydown);
		}
	};
}
