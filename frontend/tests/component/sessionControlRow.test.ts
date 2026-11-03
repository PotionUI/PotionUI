import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';
import type { Session } from '$lib/types/api';
import { keyboardFocus } from './helpers/keyboardFocus';

const { default: SessionControl } = await import('$lib/components/session/SessionControl.svelte');
const { sideDrawer } = await import('$lib/stores/sideDrawer');

const LONG_NAME = 'Portrait study, rim light, 85mm, overcast rooftop, second pass';

function makeSession(id: string, name: string): Session {
	const stamp = new Date(Date.now() - 120_000).toISOString();
	return { id, preset_id: 'p', name, data: {}, created_at: stamp, updated_at: stamp, version: 1 };
}

function state(overrides: Record<string, unknown> = {}) {
	const current = makeSession('a', 'Neon alley');
	return {
		sessions: [current, makeSession('b', 'Forest fog')],
		currentSession: current as Session | null,
		selectedSessionId: 'a',
		isSessionLoading: false,
		isQuickSaving: false,
		hasUnsavedChanges: false,
		lastSavedTime: new Date(),
		sessionControlsEnabled: true,
		autoSaveEnabled: false,
		autoSaveInterval: 10000,
		historySessionId: null,
		historyVersions: [],
		isHistoryLoading: false,
		historyError: null,
		isRestoringVersion: false,
		...overrides
	};
}

function actions() {
	return {
		select: vi.fn(),
		save: vi.fn(),
		saveAs: vi.fn(),
		startNew: vi.fn(),
		rename: vi.fn(),
		remove: vi.fn(),
		togglePin: vi.fn(),
		toggleAutosave: vi.fn(),
		setAutosaveInterval: vi.fn(),
		openHistory: vi.fn(),
		closeHistory: vi.fn(),
		restoreVersion: vi.fn(),
		reload: vi.fn()
	};
}

let instance: ReturnType<typeof mount> | undefined;
let target: HTMLElement;

function render(sessionOverrides: Record<string, unknown> = {}, extraProps: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const spies = actions();
	instance = mount(SessionControl as never, { target, props: { session: state(sessionOverrides), actions: spies, ...extraProps } });
	flushSync();
	return spies;
}

function trigger(): HTMLButtonElement {
	return target.querySelector<HTMLButtonElement>('[data-testid="session-trigger"]')!;
}

function saveSegment(): HTMLButtonElement {
	return Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((el) => el !== trigger())!;
}

function dialog(): HTMLElement | null {
	return document.body.querySelector('[role="dialog"][aria-label="Sessions"]');
}

function pointerDown(el: Element) {
	el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
	flushSync();
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

afterEach(async () => {
	if (instance) unmount(instance);
	instance = undefined;
	sideDrawer.close('sessions');
	sideDrawer.close('formulas');
	await settle();
	document.body.innerHTML = '';
});

describe('SessionControl save segment', () => {
	it('saves a draft that has no session yet as a new session', () => {
		const spies = render({ currentSession: null, selectedSessionId: '' });
		expect(saveSegment().getAttribute('aria-label')).toBe('Save as a new session');
		expect(saveSegment().textContent?.trim()).toBe('Save');
		expect(trigger().textContent).toContain('No saved session');
		saveSegment().click();
		expect(spies.saveAs).toHaveBeenCalledTimes(1);
		expect(spies.save).not.toHaveBeenCalled();
	});

	it('saves unsaved changes into the current session', () => {
		const spies = render({ hasUnsavedChanges: true });
		expect(saveSegment().getAttribute('aria-label')).toBe('Save session');
		saveSegment().click();
		expect(spies.save).toHaveBeenCalledTimes(1);
		expect(spies.saveAs).not.toHaveBeenCalled();
	});

	it('stays in place as a disabled Save while clean and reads Saving while a save runs', () => {
		const spies = render();
		expect(saveSegment().getAttribute('aria-label')).toBe('Session saved');
		expect(saveSegment().textContent?.trim()).toBe('Save');
		expect(saveSegment().getAttribute('aria-disabled')).toBe('true');
		saveSegment().click();
		expect(spies.save).not.toHaveBeenCalled();
		expect(spies.saveAs).not.toHaveBeenCalled();
		unmount(instance!);
		instance = undefined;
		target.remove();
		render({ isQuickSaving: true, hasUnsavedChanges: true });
		expect(saveSegment().getAttribute('aria-label')).toBe('Saving session');
		expect(saveSegment().textContent?.trim()).toBe('Saving');
	});

	it('disables the row until a preset and mode are chosen', () => {
		const spies = render({ sessionControlsEnabled: false, currentSession: null, selectedSessionId: '' });
		expect(saveSegment().getAttribute('aria-label')).toBe('Session save unavailable');
		expect(saveSegment().getAttribute('aria-disabled')).toBe('true');
		expect(trigger().disabled).toBe(true);
		expect(trigger().textContent).toContain('Session unavailable');
		trigger().click();
		flushSync();
		expect(dialog()).toBeNull();
		expect(spies.saveAs).not.toHaveBeenCalled();
	});
});

describe('SessionControl trigger', () => {
	it('keeps a long name on one truncated line and uses no native title', () => {
		render({ currentSession: makeSession('a', LONG_NAME) });
		const name = trigger().querySelector('span.truncate');
		expect(name?.textContent).toBe(LONG_NAME);
		expect(target.querySelectorAll('[title]').length).toBe(0);
	});

	it('opens the sessions dropdown, claims the side-drawer slot and closes on a second click', async () => {
		render();
		trigger().click();
		flushSync();
		expect(dialog()).not.toBeNull();
		expect(trigger().getAttribute('aria-expanded')).toBe('true');
		expect(get(sideDrawer)).toBe('sessions');
		pointerDown(trigger());
		trigger().click();
		flushSync();
		await settle();
		expect(dialog()).toBeNull();
		expect(get(sideDrawer)).toBeNull();
	});

	it('closes when Formulas takes the side-drawer slot', async () => {
		render();
		trigger().click();
		flushSync();
		sideDrawer.open('formulas');
		flushSync();
		await settle();
		expect(dialog()).toBeNull();
	});

	it('closes on a click outside but not on a click on its own Save segment', async () => {
		const spies = render({ hasUnsavedChanges: true });
		trigger().click();
		flushSync();
		pointerDown(saveSegment());
		saveSegment().click();
		flushSync();
		expect(spies.save).toHaveBeenCalledTimes(1);
		expect(dialog()).not.toBeNull();
		const outside = document.createElement('div');
		document.body.appendChild(outside);
		pointerDown(outside);
		await settle();
		expect(dialog()).toBeNull();
	});
});

describe('SessionControl select', () => {
	it('is an unfilled select with a value row and no chip box', () => {
		render();
		const select = trigger();
		expect(select.classList.contains('ctx-select')).toBe(true);
		expect(select.classList.contains('ctx-select-session')).toBe(true);
		expect(Array.from(select.classList).filter((token) => /^(hover:)?bg-|^rounded|^px-|^h-(8|full)$|^border/.test(token))).toEqual([]);
		const row = select.querySelector('.ctx-valuerow')!;
		expect(row.textContent).toContain('Neon alley');
		expect(row.nextElementSibling!.tagName.toLowerCase()).toBe('svg');
	});

	it('is named by the visible label and the session name when a label is given, hiding nothing', () => {
		const label = document.createElement('span');
		label.id = 'session-label';
		label.textContent = 'Session';
		document.body.appendChild(label);
		render({}, { labelId: 'session-label' });
		const select = trigger();
		const valueId = select.querySelector('.ctx-valuerow span:last-child')!.id;
		expect(valueId).not.toBe('');
		expect(select.getAttribute('aria-labelledby')).toBe(`session-label ${valueId}`);
		expect(select.hasAttribute('aria-label')).toBe(false);
		expect(`${label.textContent} ${document.getElementById(valueId)!.textContent}`).toBe('Session Neon alley');
		label.remove();
	});

	it('falls back to a plain Session name when no label is given', () => {
		render();
		expect(trigger().getAttribute('aria-label')).toBe('Session');
		expect(trigger().hasAttribute('aria-labelledby')).toBe(false);
	});

	it('marks itself open exactly while its dropdown is open', async () => {
		render();
		expect(trigger().classList.contains('is-open')).toBe(false);
		trigger().click();
		flushSync();
		expect(trigger().getAttribute('aria-expanded')).toBe('true');
		expect(trigger().classList.contains('is-open')).toBe(true);
		expect(trigger().querySelector('svg')!.getAttribute('class')).toContain('rotate-180');
	});

	it('sits in a segment that lets the select, Save and Reload share the rule baseline', () => {
		render({ hasUnsavedChanges: true });
		const segment = target.querySelector<HTMLElement>('[data-testid="context-session"]')!;
		expect(segment.classList.contains('ctx-seg')).toBe(true);
		expect(segment.classList.contains('items-center')).toBe(false);
		expect(segment.classList.contains('h-8')).toBe(false);
		expect(segment.contains(saveSegment())).toBe(true);
		expect(segment.contains(target.querySelector('[data-testid="session-reload"]'))).toBe(true);
	});

	it('keeps Save and Reload as buttons outside the select', () => {
		render({ hasUnsavedChanges: true });
		expect(trigger().contains(saveSegment())).toBe(false);
		expect(saveSegment().classList.contains('bg-surface-3')).toBe(true);
		expect(saveSegment().classList.contains('h-8')).toBe(true);
		expect(saveSegment().classList.contains('h-7')).toBe(false);
		expect(target.querySelector('[data-testid="session-reload"]')!.classList.contains('ctx-select')).toBe(false);
	});
});

describe('SessionControl reload button', () => {
	function reloadButton(): HTMLButtonElement | null {
		return target.querySelector<HTMLButtonElement>('[data-testid="session-reload"]');
	}

	function segment(): HTMLElement {
		return target.querySelector<HTMLElement>('[data-testid="context-session"]')!;
	}

	async function tooltipText(button: HTMLElement): Promise<string | undefined> {
		keyboardFocus(button);
		await Promise.resolve();
		const text = document.body.querySelector('[role="tooltip"]')?.textContent?.trim();
		button.blur();
		return text;
	}

	function rerender(sessionOverrides: Record<string, unknown> = {}) {
		unmount(instance!);
		instance = undefined;
		target.remove();
		return render(sessionOverrides);
	}

	it('sits right after Save and names itself whether or not the session has unsaved changes', () => {
		render({ hasUnsavedChanges: true });
		const dirty = reloadButton()!;
		const saveWrapper = Array.from(segment().children).find((child) => child.contains(saveSegment()))!;
		expect(saveWrapper.nextElementSibling!.contains(dirty)).toBe(true);
		expect(dirty.getAttribute('aria-label')).toBe('Reload from last snapshot');
		rerender();
		expect(reloadButton()!.getAttribute('aria-label')).toBe('Reload from last snapshot');
	});

	it('is shown disabled while the session is clean, the way Save is', () => {
		const spies = render();
		const button = reloadButton()!;
		expect(button).not.toBeNull();
		expect(button.getAttribute('aria-disabled')).toBe('true');
		expect(button.classList.contains('!cursor-not-allowed')).toBe(true);
		expect(button.classList.contains('!text-fg-disabled')).toBe(true);
		button.click();
		expect(spies.reload).not.toHaveBeenCalled();
	});

	it('is enabled while the session has unsaved changes', () => {
		const spies = render({ hasUnsavedChanges: true });
		const button = reloadButton()!;
		expect(button.getAttribute('aria-disabled')).toBeNull();
		expect(button.classList.contains('!cursor-not-allowed')).toBe(false);
		button.click();
		expect(spies.reload).toHaveBeenCalledTimes(1);
		expect(spies.select).not.toHaveBeenCalled();
		expect(spies.save).not.toHaveBeenCalled();
	});

	it('explains itself in the tooltip: the action while dirty, why it is idle while clean', async () => {
		render({ hasUnsavedChanges: true });
		expect(await tooltipText(reloadButton()!)).toBe('Reload from last snapshot');
		rerender();
		expect(await tooltipText(reloadButton()!)).toBe('No unsaved changes to discard');
	});

	it('is absent, with no room kept for it, when there is no saved session', () => {
		render({ currentSession: null, selectedSessionId: '', hasUnsavedChanges: true });
		expect(reloadButton()).toBeNull();
		expect(segment().classList.contains('min-w-[224px]')).toBe(true);
	});

	it('is disabled while a save is running', () => {
		const spies = render({ hasUnsavedChanges: true, isQuickSaving: true });
		expect(reloadButton()!.getAttribute('aria-disabled')).toBe('true');
		reloadButton()!.click();
		expect(spies.reload).not.toHaveBeenCalled();
	});

	it('widens the segment by the button so the session chip keeps its minimum and maximum width', () => {
		render();
		expect(segment().classList.contains('min-w-[262px]')).toBe(true);
		expect(segment().classList.contains('max-w-[442px]')).toBe(true);
		rerender({ currentSession: null, selectedSessionId: '' });
		expect(segment().classList.contains('min-w-[224px]')).toBe(true);
		expect(segment().classList.contains('max-w-[404px]')).toBe(true);
	});

	it('makes the button as tall and wide as the 32px context tools', () => {
		render();
		const classes = reloadButton()!.classList;
		expect(classes.contains('min-h-8')).toBe(true);
		expect(classes.contains('min-w-8')).toBe(true);
		expect(classes.contains('!min-h-7')).toBe(false);
		expect(classes.contains('!min-w-7')).toBe(false);
	});

	it('keeps the segment and the button the same size whether the session is clean or dirty', () => {
		const size = ['min-h-8', 'min-w-8', '!p-0'];
		render();
		const clean = { segment: segment().className, size: size.filter((token) => reloadButton()!.classList.contains(token)) };
		expect(clean.size).toEqual(size);
		rerender({ hasUnsavedChanges: true });
		expect(segment().className).toBe(clean.segment);
		expect(size.filter((token) => reloadButton()!.classList.contains(token))).toEqual(size);
	});
});
