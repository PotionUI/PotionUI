import { get } from 'svelte/store';
import { page } from '$app/stores';
import { keybindingsStore, type KeybindingAction } from '$lib/stores/keybindings';
import { isTypingInField } from '$lib/utils/editableTarget';

let initialized = false;
let suppressGlobal = false;
let holdCount = 0;
let holdEpoch = 0;

export function init() {
	if (initialized) return;
	document.addEventListener('keydown', handleKeydown);
	initialized = true;
}

export function destroy() {
	if (!initialized) return;
	document.removeEventListener('keydown', handleKeydown);
	initialized = false;
}

export function suppressKeyboard() {
	suppressGlobal = true;
}

export function resumeKeyboard() {
	suppressGlobal = false;
}

export function holdKeyboard(): () => void {
	holdCount++;
	const epoch = holdEpoch;
	let held = true;
	return () => {
		if (!held || epoch !== holdEpoch) return;
		held = false;
		releaseKeyboardHold();
	};
}

export function releaseKeyboardHold(): void {
	if (holdCount > 0) holdCount--;
}

export function releaseAllKeyboardHolds(): void {
	holdCount = 0;
	holdEpoch++;
}

export function isKeyboardSuppressed(): boolean {
	return suppressGlobal || holdCount > 0;
}

export function shouldIgnoreEvent(e: KeyboardEvent): boolean {
	return isTypingInField(e) && !(e.ctrlKey || e.metaKey || e.altKey);
}

function getContext(pathname: string): string {
	if (pathname.startsWith('/generate')) return 'generate';
	if (pathname.startsWith('/history')) return 'history';
	if (pathname.startsWith('/models')) return 'models';
	if (pathname.startsWith('/admin')) return 'admin';
	return 'global';
}

function normalizeKey(e: KeyboardEvent): { key: string; modifiers: string } {
	const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

	// For non-letter single characters (like ?, !, @, #), shift is implicit
	// in producing the character - don't treat it as a modifier
	const isImplicitShift = e.key.length === 1 && e.shiftKey && !/^[a-zA-Z]$/.test(e.key);

	const modifiers = [
		e.ctrlKey && 'ctrl',
		e.shiftKey && !isImplicitShift && 'shift',
		e.altKey && 'alt',
		e.metaKey && 'meta'
	]
		.filter(Boolean)
		.join(',');

	return { key, modifiers };
}

export function globalBindingFor(e: KeyboardEvent): KeybindingAction | null {
	if (isKeyboardSuppressed() || shouldIgnoreEvent(e)) return null;

	const { key, modifiers } = normalizeKey(e);
	const candidates = get(keybindingsStore).bindings.filter(
		(b) => b.enabled && b.handler && b.key?.toLowerCase() === key && (b.modifiers || '') === modifiers
	);
	if (!candidates.length) return null;
	const currentContext = getContext(get(page).url.pathname);
	return candidates.find((b) => b.context === 'global' || b.context === currentContext) ?? null;
}

function handleKeydown(e: KeyboardEvent) {
	if (e.defaultPrevented) return;
	const match = globalBindingFor(e);
	if (!match?.handler) return;
	e.preventDefault();
	e.stopPropagation();
	match.handler();
}
