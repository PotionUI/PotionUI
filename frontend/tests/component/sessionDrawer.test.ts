// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Session, SessionVersionSummary } from '$lib/types/api';

const { default: SessionDrawer } = await import('$lib/components/session/SessionDrawer.svelte');

function makeSession(id: string, name: string, minutesAgo = 5): Session {
	const stamp = new Date(Date.now() - minutesAgo * 60_000).toISOString();
	return { id, preset_id: 'p', name, data: {}, created_at: stamp, updated_at: stamp };
}

function makeVersion(n: number, extra: Partial<SessionVersionSummary> = {}): SessionVersionSummary {
	return {
		version_number: n,
		created_at: new Date(Date.now() - n * 3_600_000).toISOString(),
		summary: 'Anima',
		...extra
	};
}

const SESSIONS = [
	makeSession('a', 'Neon alley', 5),
	makeSession('b', 'Forest fog', 90),
	makeSession('c', 'Neon market', 3000)
];

function baseProps(overrides: Record<string, unknown> = {}) {
	return {
		sessions: SESSIONS,
		currentSession: SESSIONS[0],
		selectedSessionId: 'a',
		loading: false,
		saving: false,
		dirty: false,
		autoSaveEnabled: false,
		autoSaveInterval: 10000,
		historySessionId: null as string | null,
		historyVersions: [] as SessionVersionSummary[],
		historyLoading: false,
		historyError: null as string | null,
		restoringVersion: false,
		onSelect: vi.fn(),
		onSave: vi.fn(),
		onSaveAs: vi.fn(),
		onNew: vi.fn(),
		onRename: vi.fn(),
		onDelete: vi.fn(),
		onToggleAutoSave: vi.fn(),
		onIntervalChange: vi.fn(),
		onOpenHistory: vi.fn(),
		onCloseHistory: vi.fn(),
		onRestoreVersion: vi.fn(),
		onClose: vi.fn(),
		...overrides
	};
}

let instance: ReturnType<typeof mount> | undefined;
let trigger: HTMLButtonElement | undefined;

function open(overrides: Record<string, unknown> = {}) {
	trigger = document.createElement('button');
	trigger.textContent = 'Session';
	document.body.appendChild(trigger);
	trigger.focus();
	const props = baseProps(overrides);
	instance = mount(SessionDrawer as never, { target: document.body, props });
	flushSync();
	return props;
}

function drawer(): HTMLElement {
	return document.body.querySelector<HTMLElement>('[role="dialog"][aria-label="Sessions"]')!;
}

function rows(): HTMLButtonElement[] {
	return Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-session-row]'));
}

function rowNames(): string[] {
	return rows().map((row) => row.querySelector('span')?.textContent?.trim() ?? '');
}

function button(label: string | RegExp): HTMLButtonElement {
	const match = (text: string) => (typeof label === 'string' ? text.includes(label) : label.test(text));
	const found = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
		(el) => match(el.getAttribute('aria-label') ?? '') || match((el.textContent ?? '').trim().replace(/\s+/g, ' '))
	);
	if (!found) throw new Error(`no button ${label}`);
	return found;
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

function type(input: HTMLInputElement, value: string) {
	input.value = value;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function key(target: Element, name: string) {
	target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
	flushSync();
}

function historyToggle(): HTMLButtonElement {
	return document.body.querySelector<HTMLButtonElement>('button[aria-controls="session-history-panel"]')!;
}

function expandHistory() {
	historyToggle().click();
	flushSync();
}

function confirmDialog(): HTMLElement | null {
	return document.body.querySelector('[role="alertdialog"]');
}

beforeEach(() => {
	Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
	localStorage.clear();
});

afterEach(async () => {
	if (instance) unmount(instance);
	instance = undefined;
	trigger?.remove();
	await settle();
	document.body.innerHTML = '';
});

describe('SessionDrawer list', () => {
	it('shows the count, keeps the current session out of the list and uses no native title', async () => {
		open();
		await settle();
		expect(drawer().querySelector('h2')?.textContent).toBe('Sessions');
		expect(drawer().textContent).toContain('3');
		expect(rowNames()).toEqual(['Forest fog', 'Neon market']);
		expect(document.body.querySelector('[data-testid="current-session-name"]')?.textContent).toBe('Neon alley');
		expect(document.body.querySelectorAll('[title]').length).toBe(0);
	});

	it('filters by name and explains an empty result', async () => {
		open();
		await settle();
		const search = document.body.querySelector<HTMLInputElement>('input[aria-label="Search sessions"]')!;
		type(search, 'neon');
		expect(rowNames()).toEqual(['Neon market']);
		type(search, 'zzz');
		expect(rows()).toHaveLength(0);
		expect(drawer().textContent).toContain('No sessions match');
	});

	it('focuses the search on slash', async () => {
		open();
		await settle();
		const search = document.body.querySelector<HTMLInputElement>('input[aria-label="Search sessions"]')!;
		key(rows()[0], '/');
		expect(document.activeElement).toBe(search);
	});
});

describe('SessionDrawer loading', () => {
	it('loads the clicked session and closes the drawer', async () => {
		const props = open();
		await settle();
		rows()[0].click();
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('b');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('stays open after a load when keep-open is on', async () => {
		localStorage.setItem('sessions-drawer-keep-open', '1');
		const props = open();
		await settle();
		expect(document.body.querySelector('.scrim')).toBeNull();
		rows()[0].click();
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('b');
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('asks before replacing unsaved changes and loads only once confirmed', async () => {
		const props = open({ dirty: true });
		await settle();
		rows()[0].click();
		await settle();
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(confirmDialog()).not.toBeNull();
		button(/^Confirm/).click();
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('b');
		expect(confirmDialog()).toBeNull();
	});

	it('keeps the drawer open and the current session when the discard prompt is cancelled', async () => {
		const props = open({ dirty: true });
		await settle();
		rows()[0].click();
		await settle();
		key(confirmDialog()!, 'Escape');
		await settle();
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
		expect(drawer()).not.toBeNull();
	});
});

describe('SessionDrawer new session', () => {
	it('starts a blank session through onNew, not save-as', async () => {
		const props = open();
		await settle();
		button(/^New$/).click();
		await settle();
		expect(props.onNew).toHaveBeenCalledTimes(1);
		expect(props.onSaveAs).not.toHaveBeenCalled();
	});

	it('guards New behind the unsaved-changes prompt', async () => {
		const props = open({ dirty: true });
		await settle();
		button(/^New$/).click();
		await settle();
		expect(props.onNew).not.toHaveBeenCalled();
		button(/^Confirm/).click();
		await settle();
		expect(props.onNew).toHaveBeenCalledTimes(1);
	});

	it('routes Save as new, rename and delete to their own handlers', async () => {
		const props = open({ dirty: true });
		await settle();
		button('Save as new').click();
		button('Rename session').click();
		button('Delete session').click();
		expect(props.onSaveAs).toHaveBeenCalledTimes(1);
		expect(props.onRename).toHaveBeenCalledTimes(1);
		expect(props.onDelete).toHaveBeenCalledTimes(1);
		expect(props.onSelect).not.toHaveBeenCalled();
	});

	it('disables Save while the session is clean', async () => {
		open({ dirty: false });
		await settle();
		expect(button(/^Save$/).disabled).toBe(true);
	});
});

describe('SessionDrawer versions', () => {
	it('opens the current session history on mount and the chevron expands another', async () => {
		const props = open();
		await settle();
		expect(props.onOpenHistory).toHaveBeenCalledWith('a');
		button('Show versions of Forest fog').click();
		expect(props.onOpenHistory).toHaveBeenCalledWith('b');
	});

	it('collapses an expanded session through the chevron', async () => {
		const props = open({ historySessionId: 'b', historyVersions: [makeVersion(1)] });
		await settle();
		button('Hide versions of Forest fog').click();
		expect(props.onCloseHistory).toHaveBeenCalled();
	});

	it('renders version, time, prompt preview and changes, falling back to the summary', async () => {
		open({
			historySessionId: 'b',
			historyVersions: [
				makeVersion(3, { prompt_preview: 'neon alley at night…', changes: ['prompt', 'size'] }),
				makeVersion(2)
			]
		});
		await settle();
		const versionRows = Array.from(document.body.querySelectorAll('[data-version-row]'));
		expect(versionRows).toHaveLength(2);
		const [rich, plain] = versionRows.map((row) => row.textContent ?? '');
		expect(rich).toContain('v3');
		expect(rich).toContain('3h ago');
		expect(rich).toContain('neon alley at night…');
		expect(rich).toContain('changed prompt, size');
		expect(plain).toContain('v2');
		expect(plain).toContain('Anima');
		expect(plain).not.toContain('changed');
	});

	it('renders changed fields with their form labels, a humanized fallback and a +N more tooltip', async () => {
		open({
			fieldLabels: { diffusion_model: 'Checkpoint' },
			historySessionId: 'b',
			historyVersions: [
				makeVersion(2, { changes: ['prompt'], changed_fields: ['diffusion_model', 'cfg_scale', 'steps', 'seed'] })
			]
		});
		await settle();
		const text = document.body.querySelector('[data-version-row="2"]')!.textContent ?? '';
		expect(text).toContain('changed prompt, Checkpoint, cfg scale');
		expect(text).toContain('+2 more');
	});

	it('restores a version through the callback', async () => {
		const props = open({ historySessionId: 'b', historyVersions: [makeVersion(4)] });
		await settle();
		(document.body.querySelector('[data-version-row="4"]') as HTMLButtonElement).click();
		await settle();
		expect(props.onRestoreVersion).toHaveBeenCalledWith('b', 4);
	});

	it('shows the newest five inline and pages the rest in a full step', async () => {
		const versions = Array.from({ length: 41 }, (_, i) => makeVersion(41 - i));
		const props = open({ historySessionId: 'a', historyVersions: versions });
		await settle();
		expandHistory();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(5);
		button(/All 41 versions/).click();
		await settle();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(20);
		expect(drawer().textContent).toContain('41 versions');
		button(/Show 20 older/).click();
		await settle();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(40);
		expect(drawer().textContent).toContain('1 left');
		key(drawer(), 'Escape');
		await settle();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(5);
		expect(props.onClose).not.toHaveBeenCalled();
	});
});

describe('SessionDrawer current history fold', () => {
	it('starts folded with only the summary header and expands on click', async () => {
		const versions = [makeVersion(3), makeVersion(2), makeVersion(1)];
		open({ historySessionId: 'a', historyVersions: versions });
		await settle();
		expect(historyToggle().getAttribute('aria-expanded')).toBe('false');
		expect(historyToggle().textContent).toContain('3 versions');
		expect(historyToggle().textContent).toContain('latest 3h ago');
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(0);
		expandHistory();
		expect(historyToggle().getAttribute('aria-expanded')).toBe('true');
		expect(document.getElementById('session-history-panel')).not.toBeNull();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(3);
		expandHistory();
		expect(document.body.querySelectorAll('[data-version-row]')).toHaveLength(0);
	});

	it('never persists the fold state', async () => {
		open({ historySessionId: 'a', historyVersions: [makeVersion(1)] });
		await settle();
		expandHistory();
		expect(localStorage.length).toBe(0);
	});
});

describe('SessionDrawer discard copy', () => {
	it('names the action in the discard prompt', async () => {
		open({ dirty: true, historySessionId: 'b', historyVersions: [makeVersion(4)] });
		await settle();
		rows()[0].click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Loading another session replaces them.');
		key(confirmDialog()!, 'Escape');
		await settle();
		(document.body.querySelector('[data-version-row="4"]') as HTMLButtonElement).click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Restoring version v4 replaces them.');
		key(confirmDialog()!, 'Escape');
		await settle();
		button(/^New$/).click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Starting a new session clears them.');
		expect(confirmDialog()!.textContent).not.toContain('Loading another session');
	});
});

describe('SessionDrawer keyboard and focus', () => {
	it('clears the search on the first Escape and closes on the second', async () => {
		const props = open();
		await settle();
		const search = document.body.querySelector<HTMLInputElement>('input[aria-label="Search sessions"]')!;
		type(search, 'fog');
		key(search, 'Escape');
		expect(search.value).toBe('');
		expect(props.onClose).not.toHaveBeenCalled();
		key(search, 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('moves through the rows with the arrow keys and loads the first match on Enter', async () => {
		const props = open();
		await settle();
		const search = document.body.querySelector<HTMLInputElement>('input[aria-label="Search sessions"]')!;
		search.focus();
		key(search, 'ArrowDown');
		const first = document.activeElement as HTMLElement;
		expect(first.hasAttribute('data-nav')).toBe(true);
		const order = Array.from(document.body.querySelectorAll('[data-nav]'));
		key(first, 'ArrowDown');
		expect(document.activeElement).toBe(order[order.indexOf(first) + 1]);
		key(document.activeElement!, 'ArrowUp');
		expect(document.activeElement).toBe(first);

		type(search, 'market');
		key(search, 'Enter');
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('c');
	});

	it('keeps focus inside the drawer after leaving the versions step so Escape still closes it', async () => {
		const versions = Array.from({ length: 8 }, (_, i) => makeVersion(8 - i));
		const props = open({ historySessionId: 'a', historyVersions: versions });
		await settle();
		expandHistory();
		button(/All 8 versions/).click();
		await settle();
		key(document.activeElement!, 'Escape');
		await settle();
		expect(drawer().contains(document.activeElement)).toBe(true);
		key(document.activeElement!, 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('closes on Escape even when a re-render dropped focus to the page body', async () => {
		const props = open();
		await settle();
		(document.activeElement as HTMLElement).blur();
		key(document.body, 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('expands and collapses a row with the right and left arrows', async () => {
		const props = open();
		await settle();
		const row = rows()[0];
		key(row, 'ArrowRight');
		expect(props.onOpenHistory).toHaveBeenCalledWith('b');
	});

	it('returns focus to the trigger when it closes', async () => {
		open();
		await settle();
		await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
		expect(drawer().contains(document.activeElement)).toBe(true);
		unmount(instance!);
		instance = undefined;
		await settle();
		expect(document.activeElement).toBe(trigger);
	});
});
