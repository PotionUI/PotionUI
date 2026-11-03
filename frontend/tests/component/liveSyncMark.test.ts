import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';
import type { Writable } from 'svelte/store';

vi.mock('$lib/stores/sessionLiveSync', async () => {
	const { writable } = await import('svelte/store');
	return { sessionLiveSync: { marks: writable({}), resolve: vi.fn() } };
});

vi.mock('$lib/stores/tabs', async () => {
	const { writable } = await import('svelte/store');
	return {
		tabsStore: writable({
			tabs: [{ id: 't1', kind: 'generation', selectedMode: 'txt2img' }],
			activeTabId: 't1'
		})
	};
});

const { sessionLiveSync } = await import('$lib/stores/sessionLiveSync');
const { default: LiveSyncMark } = await import('$lib/components/session/LiveSyncMark.svelte');
const { default: LiveSyncPending } = await import('$lib/components/session/LiveSyncPending.svelte');
const { default: BookTab } = await import('$lib/components/BookTab.svelte');

const marks = sessionLiveSync.marks as unknown as Writable<Record<string, unknown>>;
const resolve = vi.mocked(sessionLiveSync.resolve);

const steps = {
	id: 'txt2img|form:steps',
	mode: 'txt2img',
	key: 'form:steps',
	local: 25,
	remote: 30,
	label: 'Claude Code'
};

const field = createRawSnippet(() => ({ render: () => '<input aria-label="Steps" value="25" />' }));

const mounted: ReturnType<typeof mount>[] = [];
let target: HTMLElement;

function render(component: never, props: Record<string, unknown>) {
	const instance = mount(component, { target, props });
	mounted.push(instance);
	flushSync();
	return instance;
}

function setMarks(value: Record<string, unknown>) {
	marks.set(value);
	flushSync();
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	resolve.mockReset();
	marks.set({});
});

afterEach(() => {
	while (mounted.length) unmount(mounted.pop()!);
	target.remove();
});

describe('LiveSyncMark', () => {
	it('renders its field untouched while nothing changed remotely', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:steps', children: field });

		expect(target.querySelector('input')).not.toBeNull();
		expect(target.querySelector('[data-testid="live-sync-highlight"]')).toBeNull();
		expect(target.querySelector('[data-testid="live-sync-choice"]')).toBeNull();
	});

	it('flashes the field a remote change touched and drops the flash when the mark expires', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:steps', children: field });

		setMarks({ t1: { sessionId: 's1', highlights: { 'form:steps': 1 }, conflicts: [], unseen: false } });
		expect(target.querySelector('[data-testid="live-sync-highlight"]')).not.toBeNull();

		setMarks({});
		expect(target.querySelector('[data-testid="live-sync-highlight"]')).toBeNull();
	});

	it('does not flash a field the change did not touch', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:cfg', children: field });

		setMarks({ t1: { sessionId: 's1', highlights: { 'form:steps': 1 }, conflicts: [], unseen: false } });

		expect(target.querySelector('[data-testid="live-sync-highlight"]')).toBeNull();
	});

	it('offers the inline choice on the conflicting field only and resolves through the live sync', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:steps', children: field });
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:cfg', children: field });

		setMarks({ t1: { sessionId: 's1', highlights: {}, conflicts: [steps], unseen: false } });

		const choices = target.querySelectorAll('[data-testid="live-sync-choice"]');
		expect(choices).toHaveLength(1);
		expect(choices[0].textContent).toContain('Changed by Claude Code');
		const [theirs, mine] = Array.from(choices[0].querySelectorAll('button'));
		expect(theirs.textContent?.trim()).toBe('Use theirs');
		expect(mine.textContent?.trim()).toBe('Keep mine');

		theirs.click();
		mine.click();
		expect(resolve.mock.calls).toEqual([
			['t1', 'txt2img|form:steps', 'theirs'],
			['t1', 'txt2img|form:steps', 'mine']
		]);
	});

	it('leaves a conflict from another mode to the pending strip', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:steps', children: field });
		render(LiveSyncPending as never, { tabId: 't1' });

		setMarks({ t1: { sessionId: 's1', highlights: {}, conflicts: [{ ...steps, id: 'img2img|form:steps', mode: 'img2img' }], unseen: false } });

		const choices = target.querySelectorAll('[data-testid="live-sync-choice"]');
		expect(choices).toHaveLength(1);
		expect(target.querySelector('[data-testid="live-sync-pending"]')?.textContent).toContain('img2img · steps');
	});
});

describe('LiveSyncPending', () => {
	it('lists a conflict no field on screen can show, and skips one a field already shows', () => {
		render(LiveSyncMark as never, { tabId: 't1', fieldKey: 'form:steps', children: field });
		render(LiveSyncPending as never, { tabId: 't1' });
		const variant = { id: 'txt2img|selectedVariant', mode: 'txt2img', key: 'selectedVariant', local: 'hq', remote: 'turbo', label: 'another window' };

		setMarks({ t1: { sessionId: 's1', highlights: {}, conflicts: [steps, variant], unseen: false } });

		const pending = target.querySelector('[data-testid="live-sync-pending"]')!;
		expect(pending.querySelectorAll('[data-testid="live-sync-choice"]')).toHaveLength(1);
		expect(pending.textContent).toContain('Changed by another window');
		expect(pending.textContent).toContain('Variant');
		pending.querySelector('button')!.click();
		expect(resolve).toHaveBeenCalledWith('t1', 'txt2img|selectedVariant', 'theirs');
	});
});

describe('BookTab remote change dot', () => {
	it('shows the unseen-change dot only when a remote change is waiting', () => {
		const base = { id: 't2', name: 'Balcony', isActive: false };
		render(BookTab as never, { ...base, remoteChange: 'Claude Code changed this session' });
		expect(target.querySelector('[data-testid="tab-remote-change"]')).not.toBeNull();
		expect(target.textContent).toContain('claude code changed this session');

		while (mounted.length) unmount(mounted.pop()!);
		render(BookTab as never, { ...base, remoteChange: null });
		expect(target.querySelector('[data-testid="tab-remote-change"]')).toBeNull();
	});
});
