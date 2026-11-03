import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Session, SessionVersionSummary } from '$lib/types/api';
import { keyboardFocus } from './helpers/keyboardFocus';

const { default: SessionPanel } = await import('$lib/components/session/SessionPanel.svelte');

function makeSession(id: string, name: string, minutesAgo = 5): Session {
	const stamp = new Date(Date.now() - minutesAgo * 60_000).toISOString();
	return { id, preset_id: 'p', name, data: {}, created_at: stamp, updated_at: stamp, version: 1 };
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
		currentSession: SESSIONS[0] as Session | null,
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
		fullscreen: false,
		fieldLabels: undefined as Record<string, string> | undefined,
		onSelect: vi.fn(),
		onSave: vi.fn(),
		onSaveAs: vi.fn(),
		onNew: vi.fn(),
		onRename: vi.fn(),
		onDelete: vi.fn(),
		onTogglePin: vi.fn(),
		onToggleAutoSave: vi.fn(),
		onIntervalChange: vi.fn(),
		onOpenHistory: vi.fn(),
		onCloseHistory: vi.fn(),
		onRestoreVersion: vi.fn(),
		onReload: vi.fn(),
		onClose: vi.fn(),
		...overrides
	};
}

function panelProps(p: ReturnType<typeof baseProps>, anchor: HTMLElement) {
	return {
		fieldLabels: p.fieldLabels,
		anchor,
		fullscreen: p.fullscreen,
		onClose: p.onClose,
		session: {
			sessions: p.sessions,
			currentSession: p.currentSession,
			selectedSessionId: p.selectedSessionId,
			isSessionLoading: p.loading,
			isQuickSaving: p.saving,
			hasUnsavedChanges: p.dirty,
			lastSavedTime: null,
			sessionControlsEnabled: true,
			autoSaveEnabled: p.autoSaveEnabled,
			autoSaveInterval: p.autoSaveInterval,
			historySessionId: p.historySessionId,
			historyVersions: p.historyVersions,
			isHistoryLoading: p.historyLoading,
			historyError: p.historyError,
			isRestoringVersion: p.restoringVersion
		},
		actions: {
			select: p.onSelect,
			save: p.onSave,
			saveAs: p.onSaveAs,
			startNew: p.onNew,
			rename: p.onRename,
			remove: p.onDelete,
			togglePin: p.onTogglePin,
			toggleAutosave: p.onToggleAutoSave,
			setAutosaveInterval: p.onIntervalChange,
			openHistory: p.onOpenHistory,
			closeHistory: p.onCloseHistory,
			restoreVersion: p.onRestoreVersion,
			reload: p.onReload
		}
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
	instance = mount(SessionPanel as never, { target: document.body, props: panelProps(props, trigger) });
	flushSync();
	return props;
}

function panel(): HTMLElement {
	return document.body.querySelector<HTMLElement>('[role="dialog"][aria-label="Sessions"]')!;
}

function rows(): HTMLButtonElement[] {
	return Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-session-row]'));
}

function rowNames(): string[] {
	return rows().map((row) => row.querySelector('span')?.textContent?.trim() ?? '');
}

function versionRows(): HTMLButtonElement[] {
	return Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-version-row]'));
}

function button(label: string | RegExp): HTMLButtonElement {
	const match = (text: string) => (typeof label === 'string' ? text.includes(label) : label.test(text));
	const found = Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find(
		(el) => match(el.getAttribute('aria-label') ?? '') || match((el.textContent ?? '').trim().replace(/\s+/g, ' '))
	);
	if (!found) throw new Error(`no button ${label}`);
	return found;
}

function search(): HTMLInputElement {
	return document.body.querySelector<HTMLInputElement>('input[aria-label="Search sessions"]')!;
}

function menu(): HTMLElement | null {
	return document.body.querySelector<HTMLElement>('[role="menu"]');
}

function menuItems(): string[] {
	return Array.from(menu()?.querySelectorAll('[role="menuitem"], [role="menuitemradio"]') ?? []).map(
		(el) => el.textContent?.trim() ?? ''
	);
}

function openRowMenu(name: string) {
	button(`More actions for ${name}`).click();
	flushSync();
}

function chooseMenuItem(label: string) {
	const item = Array.from(menu()!.querySelectorAll<HTMLButtonElement>('button')).find(
		(el) => el.textContent?.trim() === label
	);
	if (!item) throw new Error(`no menu item ${label}`);
	item.click();
	flushSync();
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

describe('SessionPanel list', () => {
	it('lists every session newest first, marks the current one and uses no native title', async () => {
		open();
		await settle();
		expect(rowNames()).toEqual(['Neon alley', 'Forest fog', 'Neon market']);
		const current = document.body.querySelectorAll('[data-session-row][aria-current="true"]');
		expect(Array.from(current).map((el) => el.getAttribute('data-session-row'))).toEqual(['a']);
		expect(search().placeholder).toBe('Search 3 sessions');
		expect(panel().querySelector('h2')).toBeNull();
		expect(document.body.querySelectorAll('[title]').length).toBe(0);
	});

	it('keeps each row to one line of name and relative time', async () => {
		open();
		await settle();
		const row = rows()[1];
		expect(row.textContent?.replace(/\s+/g, ' ').trim()).toBe('Forest fog 1h ago');
		expect(panel().textContent).not.toContain('Updated');
	});

	it('marks the current row when it has unsaved changes', async () => {
		open({ dirty: true });
		await settle();
		expect(rows()[0].textContent).toContain('Unsaved changes');
		expect(rows()[1].textContent).not.toContain('Unsaved changes');
	});

	it('filters by name, counts the matches, bolds the match and explains an empty result', async () => {
		open();
		await settle();
		type(search(), 'neon');
		expect(rowNames()).toEqual(['Neon alley', 'Neon market']);
		expect(panel().textContent).toContain('2 of 3');
		expect(rows()[0].querySelector('b')?.textContent).toBe('Neon');
		type(search(), 'zzz');
		expect(rows()).toHaveLength(0);
		expect(panel().textContent).toContain('No sessions match “zzz”');
	});

	it('focuses the search on slash', async () => {
		open();
		await settle();
		key(rows()[0], '/');
		expect(document.activeElement).toBe(search());
	});

	it('shows a titled screen with its own close button when full screen', async () => {
		const props = open({ fullscreen: true });
		await settle();
		expect(panel().querySelector('h2')?.textContent).toBe('Sessions');
		button('Close sessions').click();
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});
});

describe('SessionPanel loading', () => {
	it('loads the clicked session and closes the panel', async () => {
		const props = open();
		await settle();
		rows()[1].click();
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('b');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('closes without reloading when the current session is picked', async () => {
		const props = open();
		await settle();
		rows()[0].click();
		await settle();
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('asks before replacing unsaved changes and loads only once confirmed', async () => {
		const props = open({ dirty: true });
		await settle();
		rows()[1].click();
		await settle();
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(confirmDialog()).not.toBeNull();
		button(/^Confirm/).click();
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('b');
		expect(confirmDialog()).toBeNull();
	});

	it('keeps the panel open and the current session when the discard prompt is cancelled', async () => {
		const props = open({ dirty: true });
		await settle();
		rows()[1].click();
		await settle();
		key(confirmDialog()!, 'Escape');
		await settle();
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
		expect(panel()).not.toBeNull();
	});
});

describe('SessionPanel reload row', () => {
	function reloadRow(): HTMLElement | null {
		return document.body.querySelector<HTMLElement>('[data-testid="session-reload-row"]');
	}

	it('offers Reload last snapshot above the list while the current session has unsaved changes', async () => {
		open({ dirty: true });
		await settle();
		expect(reloadRow()).not.toBeNull();
		expect(reloadRow()!.textContent).toContain('Unsaved changes');
		expect(reloadRow()!.querySelector('button')!.textContent!.trim()).toBe('Reload last snapshot');
		const list = document.body.querySelector('[data-testid="session-list-scroll"]')!;
		expect(reloadRow()!.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('hands the reload to the host and closes the panel without loading anything itself', async () => {
		const props = open({ dirty: true });
		await settle();
		reloadRow()!.querySelector('button')!.click();
		await settle();
		expect(props.onReload).toHaveBeenCalledTimes(1);
		expect(props.onClose).toHaveBeenCalledTimes(1);
		expect(props.onClose.mock.invocationCallOrder[0]).toBeLessThan(props.onReload.mock.invocationCallOrder[0]);
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(confirmDialog()).toBeNull();
	});

	it('is absent while the session is clean', async () => {
		open();
		await settle();
		expect(reloadRow()).toBeNull();
	});

	it('is absent for an unsaved draft that has no saved session', async () => {
		open({ dirty: true, currentSession: null, selectedSessionId: '' });
		await settle();
		expect(reloadRow()).toBeNull();
	});
});

describe('SessionPanel footer', () => {
	it('starts a blank session through New session, not save-as', async () => {
		const props = open();
		await settle();
		button(/^New session$/).click();
		await settle();
		expect(props.onNew).toHaveBeenCalledTimes(1);
		expect(props.onSaveAs).not.toHaveBeenCalled();
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('guards New session behind the unsaved-changes prompt', async () => {
		const props = open({ dirty: true });
		await settle();
		button(/^New session$/).click();
		await settle();
		expect(props.onNew).not.toHaveBeenCalled();
		button(/^Confirm/).click();
		await settle();
		expect(props.onNew).toHaveBeenCalledTimes(1);
	});

	it('opens the save-as flow from Save as new', async () => {
		const props = open();
		await settle();
		button(/^Save as new$/).click();
		expect(props.onSaveAs).toHaveBeenCalledTimes(1);
		expect(props.onSelect).not.toHaveBeenCalled();
	});
});

describe('SessionPanel row menu', () => {
	it('offers Pin and Version history on every row and adds Rename and Delete on the current one', async () => {
		open();
		await settle();
		openRowMenu('Forest fog');
		expect(menuItems()).toEqual(['Pin', 'Version history']);
		key(document.activeElement!, 'Escape');
		await settle();
		openRowMenu('Neon alley');
		expect(menuItems()).toEqual(['Pin', 'Version history', 'Rename…', 'Delete…']);
	});

	it('offers Unpin on a pinned row', async () => {
		open({ sessions: SESSIONS.map((s) => (s.id === 'c' ? { ...s, pinned: true } : s)) });
		await settle();
		openRowMenu('Neon market');
		expect(menuItems()[0]).toBe('Unpin');
	});

	it('routes pin, rename and delete through the row menu', async () => {
		const props = open();
		await settle();
		openRowMenu('Forest fog');
		chooseMenuItem('Pin');
		expect(props.onTogglePin).toHaveBeenCalledWith('b');
		openRowMenu('Neon alley');
		chooseMenuItem('Rename…');
		expect(props.onRename).toHaveBeenCalledTimes(1);
		openRowMenu('Neon alley');
		chooseMenuItem('Delete…');
		expect(props.onDelete).toHaveBeenCalledTimes(1);
		expect(props.onSelect).not.toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('closes an open row menu on Escape and leaves the panel open for the next Escape', async () => {
		const props = open();
		await settle();
		openRowMenu('Forest fog');
		expect(menu()).not.toBeNull();
		key(document.activeElement!, 'Escape');
		await settle();
		expect(menu()).toBeNull();
		expect(props.onClose).not.toHaveBeenCalled();
		expect(panel()).not.toBeNull();
		key(document.activeElement ?? document.body, 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});
});

describe('SessionPanel version history', () => {
	it('loads history only when the versions view opens', async () => {
		const props = open();
		await settle();
		expect(props.onOpenHistory).not.toHaveBeenCalled();
		openRowMenu('Forest fog');
		chooseMenuItem('Version history');
		await settle();
		expect(props.onOpenHistory).toHaveBeenCalledWith('b');
		expect(button('Back to sessions')).toBeTruthy();
		expect(panel().textContent).toContain('Forest fog');
		expect(rows()).toHaveLength(0);
	});

	it('renders one line per version with the changes in a tooltip', async () => {
		open({
			fieldLabels: { diffusion_model: 'Checkpoint' },
			historySessionId: 'b',
			historyVersions: [
				makeVersion(3, {
					prompt_preview: 'neon alley at night…',
					changes: ['prompt'],
					changed_fields: ['diffusion_model', 'cfg_scale', 'steps', 'seed']
				}),
				makeVersion(2)
			]
		});
		await settle();
		openRowMenu('Forest fog');
		chooseMenuItem('Version history');
		await settle();
		const [rich, plain] = versionRows().map((row) => row.textContent?.replace(/\s+/g, ' ').trim() ?? '');
		expect(rich).toBe('v3 neon alley at night… Latest 3h ago');
		expect(plain).toBe('v2 Anima 2h ago');
		expect(versionRows()[0].querySelector('.truncate')?.textContent).toBe('neon alley at night…');
		keyboardFocus(versionRows()[0]);
		flushSync();
		await settle();
		expect(document.body.textContent).toContain('changed prompt, Checkpoint, cfg scale, steps, seed');
	});

	it('restores a version and stays in the history view', async () => {
		const props = open({ historySessionId: 'b', historyVersions: [makeVersion(4)] });
		await settle();
		openRowMenu('Forest fog');
		chooseMenuItem('Version history');
		await settle();
		versionRows()[0].click();
		await settle();
		expect(props.onRestoreVersion).toHaveBeenCalledWith('b', 4);
		expect(props.onClose).not.toHaveBeenCalled();
		expect(versionRows()).toHaveLength(1);
	});

	it('pages twenty versions at a time and returns to the list on Escape', async () => {
		const versions = Array.from({ length: 41 }, (_, i) => makeVersion(41 - i));
		const props = open({ historySessionId: 'a', historyVersions: versions });
		await settle();
		openRowMenu('Neon alley');
		chooseMenuItem('Version history');
		await settle();
		expect(versionRows()).toHaveLength(20);
		expect(panel().textContent).toContain('41 versions');
		button(/Show 20 older/).click();
		await settle();
		expect(versionRows()).toHaveLength(40);
		button(/Show 1 older/).click();
		await settle();
		expect(versionRows()).toHaveLength(41);
		key(panel(), 'Escape');
		await settle();
		expect(versionRows()).toHaveLength(0);
		expect(rows()).toHaveLength(3);
		expect(props.onCloseHistory).toHaveBeenCalled();
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('opens a row history with the right arrow and goes back with the left arrow', async () => {
		const props = open({ historySessionId: 'b', historyVersions: [makeVersion(1)] });
		await settle();
		await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
		key(rows()[1], 'ArrowRight');
		await settle();
		expect(versionRows()).toHaveLength(1);
		key(versionRows()[0], 'ArrowLeft');
		await settle();
		expect(versionRows()).toHaveLength(0);
		expect(document.activeElement).toBe(rows()[1]);
		expect(props.onClose).not.toHaveBeenCalled();
	});

	it('names the action in the discard prompt', async () => {
		open({ dirty: true, historySessionId: 'b', historyVersions: [makeVersion(4)] });
		await settle();
		rows()[1].click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Loading another session replaces them.');
		key(confirmDialog()!, 'Escape');
		await settle();
		openRowMenu('Forest fog');
		chooseMenuItem('Version history');
		await settle();
		versionRows()[0].click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Restoring version v4 replaces them.');
		key(confirmDialog()!, 'Escape');
		await settle();
		button('Back to sessions').click();
		await settle();
		button(/^New session$/).click();
		await settle();
		expect(confirmDialog()!.textContent).toContain('Starting a new session clears them.');
	});
});

describe('SessionPanel keyboard and focus', () => {
	it('clears the search on the first Escape and closes on the second', async () => {
		const props = open();
		await settle();
		type(search(), 'fog');
		key(search(), 'Escape');
		expect(search().value).toBe('');
		expect(props.onClose).not.toHaveBeenCalled();
		key(search(), 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('moves through the rows with the arrow keys and loads the first match on Enter', async () => {
		const props = open();
		await settle();
		search().focus();
		key(search(), 'ArrowDown');
		const first = document.activeElement as HTMLElement;
		expect(first.hasAttribute('data-nav')).toBe(true);
		const order = Array.from(document.body.querySelectorAll('[data-nav]'));
		key(first, 'ArrowDown');
		expect(document.activeElement).toBe(order[order.indexOf(first) + 1]);
		key(document.activeElement!, 'ArrowUp');
		expect(document.activeElement).toBe(first);
		type(search(), 'market');
		key(search(), 'Enter');
		await settle();
		expect(props.onSelect).toHaveBeenCalledWith('c');
	});

	it('closes on Escape even when a re-render dropped focus to the page body', async () => {
		const props = open();
		await settle();
		(document.activeElement as HTMLElement).blur();
		key(document.body, 'Escape');
		expect(props.onClose).toHaveBeenCalledTimes(1);
	});

	it('returns focus to the trigger when it closes', async () => {
		open();
		await settle();
		await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
		expect(panel().contains(document.activeElement)).toBe(true);
		unmount(instance!);
		instance = undefined;
		await settle();
		expect(document.activeElement).toBe(trigger);
	});
});

describe('SessionPanel pinning', () => {
	function withPinned(id: string) {
		return SESSIONS.map((s) => (s.id === id ? { ...s, pinned: true } : s));
	}

	it('hides the Pinned label when nothing is pinned', async () => {
		open();
		await settle();
		expect(document.body.querySelector('[data-testid="pinned-head"]')).toBeNull();
	});

	it('lists pinned sessions under a Pinned label above the rest', async () => {
		open({ sessions: withPinned('c') });
		await settle();
		expect(document.body.querySelector('[data-testid="pinned-head"]')?.textContent).toBe('Pinned');
		const pinnedRows = document.body.querySelectorAll('[data-testid="pinned-list"] [data-session-row]');
		expect(Array.from(pinnedRows).map((el) => el.getAttribute('data-session-row'))).toEqual(['c']);
		expect(rowNames()).toEqual(['Neon market', 'Neon alley', 'Forest fog']);
	});

	it('filters pinned sessions with the search box', async () => {
		open({ sessions: withPinned('c') });
		await settle();
		type(search(), 'forest');
		await settle();
		expect(document.body.querySelector('[data-testid="pinned-head"]')).toBeNull();
		expect(rowNames()).toEqual(['Forest fog']);
	});
});

describe('SessionPanel auto-save menu', () => {
	function autosaveButton(): HTMLButtonElement {
		return button(/^Auto-save/);
	}

	it('shows the interval and changes it through the menu', async () => {
		const props = open({ autoSaveEnabled: true, autoSaveInterval: 10000 });
		await settle();
		expect(autosaveButton().textContent?.replace(/\s+/g, ' ').trim()).toBe('Auto-save 10s');
		autosaveButton().click();
		flushSync();
		expect(menuItems()).toEqual(['Off', 'Every 5s', 'Every 10s', 'Every 30s', 'Every minute']);
		const checked = menu()!.querySelector('[aria-checked="true"]');
		expect(checked?.textContent?.trim()).toBe('Every 10s');
		chooseMenuItem('Every 30s');
		expect(props.onIntervalChange).toHaveBeenCalledWith(30000);
		expect(props.onToggleAutoSave).not.toHaveBeenCalled();
		expect(menu()).toBeNull();
	});

	it('turns auto-save on with an interval and off with Off', async () => {
		const props = open({ autoSaveEnabled: false, autoSaveInterval: 10000 });
		await settle();
		expect(autosaveButton().textContent?.replace(/\s+/g, ' ').trim()).toBe('Auto-save off');
		autosaveButton().click();
		flushSync();
		chooseMenuItem('Every 5s');
		expect(props.onIntervalChange).toHaveBeenCalledWith(5000);
		expect(props.onToggleAutoSave).toHaveBeenCalledTimes(1);
		unmount(instance!);
		instance = undefined;
		await settle();
		document.body.innerHTML = '';
		const enabled = open({ autoSaveEnabled: true, autoSaveInterval: 5000 });
		await settle();
		autosaveButton().click();
		flushSync();
		chooseMenuItem('Off');
		expect(enabled.onToggleAutoSave).toHaveBeenCalledTimes(1);
		expect(enabled.onIntervalChange).not.toHaveBeenCalled();
	});

	it('stays disabled until the draft is saved as a session', async () => {
		open({ currentSession: null, selectedSessionId: '' });
		await settle();
		expect(autosaveButton().disabled).toBe(true);
	});
});

describe('SessionPanel with no sessions', () => {
	it('explains the empty state and offers one save action instead of a search', async () => {
		const props = open({ sessions: [], currentSession: null, selectedSessionId: '' });
		await settle();
		expect(panel().textContent).toContain('No saved sessions for this preset yet');
		expect(search()).toBeNull();
		expect(rows()).toHaveLength(0);
		expect(() => button(/^Save as new$/)).toThrow();
		button(/^Save as new session$/).click();
		expect(props.onSaveAs).toHaveBeenCalledTimes(1);
		button(/^New session$/).click();
		await settle();
		expect(props.onNew).toHaveBeenCalledTimes(1);
	});
});
