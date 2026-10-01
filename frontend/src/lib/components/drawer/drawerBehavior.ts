import { storage } from '$lib/utils/storage';

export const KEEP_OPEN_MIN_WIDTH = 1200;
export const PHONE_MAX_WIDTH = 640;

export function readKeepOpenPref(key: string): boolean {
	try {
		return storage.get(key) === '1';
	} catch {
		return false;
	}
}

export function writeKeepOpenPref(key: string, next: boolean): void {
	try {
		storage.set(key, next ? '1' : '0');
	} catch {
		return;
	}
}

export function effectiveKeepOpen(pref: boolean, viewportWidth: number): boolean {
	return pref && viewportWidth >= KEEP_OPEN_MIN_WIDTH;
}

export function isEditable(target: EventTarget | null): boolean {
	const el = target as HTMLElement | null;
	return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable === true);
}

function navItems(root: HTMLElement | undefined): HTMLElement[] {
	if (!root) return [];
	return Array.from(root.querySelectorAll<HTMLElement>('[data-nav]')).filter(
		(el) => !(el as HTMLButtonElement).disabled
	);
}

export interface DrawerKeyContext {
	root: HTMLElement | undefined;
	searchEl: HTMLInputElement | undefined;
	keepOpen: boolean;
	onEscape?: () => boolean;
	onClose: () => void;
}

export function handleDrawerKeydown(event: KeyboardEvent, context: DrawerKeyContext): void {
	const { root, searchEl, keepOpen, onEscape, onClose } = context;
	const target = event.target as HTMLElement;
	const inside = !!root && root.contains(target);
	if (!inside && !(target === document.body && !keepOpen)) return;

	if (event.key === 'Escape') {
		event.preventDefault();
		event.stopPropagation();
		if (!onEscape?.()) onClose();
		return;
	}

	if (event.key === '/' && !isEditable(target)) {
		event.preventDefault();
		searchEl?.focus();
		return;
	}

	if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
		const items = navItems(root);
		if (items.length === 0) return;
		event.preventDefault();
		const index = items.indexOf(target);
		if (event.key === 'ArrowDown') items[index < 0 ? 0 : Math.min(items.length - 1, index + 1)].focus();
		else if (index > 0) items[index - 1].focus();
		else if (index === 0) searchEl?.focus();
	}
}
