// @vitest-environment jsdom
//
// The phrasebook selection bar lists plugin-registered batch operations under
// a More menu, and only shows that menu when a non-core op is registered.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { writable } from 'svelte/store';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: { getBaseURL: vi.fn(() => ''), getToken: vi.fn(() => null), setOnAuthExpired: vi.fn() }
}));

vi.mock('$lib/stores/plugins', () => ({
	pluginStore: { loadFrontendHooks: vi.fn(async () => {}) },
	frontendHooks: writable({})
}));

const { default: PhrasebookSelectionBar } = await import(
	'../../src/routes/phrasebook/components/PhrasebookSelectionBar.svelte'
);

const TITLECASE = {
	id: 'titlecase',
	label: 'Title-case labels',
	component: 'plugin:x:Modal.svelte',
	has_preview: false,
	source: 'x'
};

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;
let onRunOp: ReturnType<typeof vi.fn>;

function mountBar(extraOps: (typeof TITLECASE)[]) {
	target = document.createElement('div');
	document.body.appendChild(target);
	onRunOp = vi.fn();
	component = mount(PhrasebookSelectionBar, {
		target,
		props: {
			selectedCount: 2,
			totalCount: 3,
			categories: [],
			extraOps,
			selectedIds: ['v1', 'v2'],
			onRunOp,
			onSelectAll: vi.fn(),
			onClear: vi.fn(),
			onReplace: vi.fn(),
			onSetActive: vi.fn(),
			onMove: vi.fn(),
			onDelete: vi.fn()
		}
	});
	flushSync();
}

beforeEach(() => {
	if (!Element.prototype.animate) {
		Element.prototype.animate = () => {
			const animation = {
				cancel() {},
				finished: Promise.resolve(),
				onfinish: null as (() => void) | null,
				currentTime: 0,
				playbackRate: 1
			};
			setTimeout(() => animation.onfinish?.(), 0);
			return animation as never;
		};
	}
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('PhrasebookSelectionBar More menu', () => {
	it('lists plugin ops under More and runs the chosen one', () => {
		mountBar([TITLECASE]);

		const more = target.querySelector<HTMLButtonElement>('[data-batch-more]');
		if (!more) throw new Error('More button missing');
		expect(more.getAttribute('aria-haspopup')).toBe('menu');
		expect(target.querySelector('[data-more-menu]')).toBeNull();

		more.click();
		flushSync();
		const item = target.querySelector<HTMLButtonElement>('[data-more-menu] [data-batch-op="titlecase"]');
		if (!item) throw new Error('menu item missing');
		expect(item.getAttribute('role')).toBe('menuitem');
		expect(item.textContent?.trim()).toBe('Title-case labels');

		item.click();
		flushSync();
		expect(onRunOp).toHaveBeenCalledWith(TITLECASE);
		expect(target.querySelector('[data-more-menu]')).toBeNull();
	});

	it('shows no More button without plugin ops', () => {
		mountBar([]);
		expect(target.querySelector('[data-batch-more]')).toBeNull();
		expect(target.textContent).toContain('Replace…');
	});
});
