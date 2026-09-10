// @vitest-environment jsdom
//
// TagSelector's panel used to render `absolute` inside a `relative inline-flex`
// trigger wrapper. Inside a scrolling ancestor (BaseModal's `overflow-auto`
// body), an absolutely positioned descendant still counts toward that
// ancestor's scrollable area — the panel opening past the modal's edge grew
// scrollbars and made the whole modal look broken. This proves the panel now
// portals to <body> as a `position: fixed` node anchored to the trigger, so it
// can never affect an ancestor's layout or scroll area.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { tick } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getTags: vi.fn().mockResolvedValue({
			success: true,
			data: { tags: [{ id: 't1', name: 'landscape' }, { id: 't2', name: 'portrait' }] }
		}),
		createTag: vi.fn()
	}
}));

const { default: TagSelector } = await import('$lib/components/TagSelector.svelte');
const { createClassComponent } = await import('svelte/legacy');

let scrollingAncestor: HTMLDivElement;
let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountInScrollingAncestor(props: Record<string, unknown> = {}) {
	scrollingAncestor = document.createElement('div');
	scrollingAncestor.className = 'overflow-auto';
	document.body.appendChild(scrollingAncestor);
	target = document.createElement('div');
	scrollingAncestor.appendChild(target);
	component = createClassComponent({
		component: TagSelector as never,
		target,
		props: { selectedTagIds: [], triggerStyle: 'pills', ...props }
	});
}

async function openPopover() {
	const trigger = target.querySelector('[role="button"]') as HTMLElement;
	trigger.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	await tick();
	await tick();
}

afterEach(() => {
	component?.$destroy?.();
	component = undefined;
	scrollingAncestor?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('TagSelector popover portal', () => {
	it('renders the open panel as a fixed, body-level node outside the scrolling ancestor', async () => {
		mountInScrollingAncestor();
		await openPopover();

		const panel = document.body.querySelector('[role="listbox"]')?.closest('.fixed') as HTMLElement;
		expect(panel).toBeTruthy();
		expect(panel.parentElement).toBe(document.body);
		expect(scrollingAncestor.contains(panel)).toBe(false);
		expect(panel.classList.contains('fixed')).toBe(true);
	});

	it('does not close on a click inside the portaled panel', async () => {
		mountInScrollingAncestor();
		await openPopover();

		const panel = document.body.querySelector('[role="listbox"]')?.closest('.fixed') as HTMLElement;
		panel.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await tick();

		expect(document.body.querySelector('[role="listbox"]')).not.toBeNull();
	});

	it('closes on a click outside the trigger and the panel', async () => {
		mountInScrollingAncestor();
		await openPopover();
		expect(document.body.querySelector('[role="listbox"]')).not.toBeNull();

		document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await tick();

		expect(document.body.querySelector('[role="listbox"]')).toBeNull();
	});
});
