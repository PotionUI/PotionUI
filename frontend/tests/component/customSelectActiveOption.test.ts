// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

const { default: CustomSelect } = await import('../../src/lib/components/CustomSelect.svelte');

const OPTIONS = [
	{ value: 'a', label: 'Alpha' },
	{ value: 'b', label: 'Beta' },
	{ value: 'c', label: 'Gamma' }
];

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
	target = null;
	document.body.innerHTML = '';
});

async function mountSelect(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(CustomSelect as never, { target, props: { options: OPTIONS, value: 'b', ...props } } as never);
	flushSync();
	const trigger = target.querySelector('button[aria-haspopup="listbox"]') as HTMLElement;
	return trigger;
}

async function key(trigger: HTMLElement, name: string) {
	trigger.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
	await tick();
	flushSync();
}

function options() {
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
}

describe('CustomSelect active option', () => {
	it('has no active descendant while closed', async () => {
		const trigger = await mountSelect();
		expect(trigger.hasAttribute('aria-activedescendant')).toBe(false);
	});

	it('points aria-activedescendant at the keyboard-active option and follows the arrow keys', async () => {
		const trigger = await mountSelect();

		await key(trigger, 'ArrowDown');
		const rows = options();
		expect(rows.every((row) => row.id)).toBe(true);
		expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
		expect(trigger.getAttribute('aria-activedescendant')).toBe(rows[1].id);

		await key(trigger, 'ArrowDown');
		expect(trigger.getAttribute('aria-activedescendant')).toBe(rows[2].id);
		expect(rows[2].getAttribute('data-active')).toBe('true');
	});

	it('gives two selects on one page distinct option ids', async () => {
		const first = await mountSelect();
		await key(first, 'ArrowDown');
		const firstId = first.getAttribute('aria-activedescendant');
		unmount(instance as never);
		instance = null;
		document.body.innerHTML = '';

		const second = await mountSelect();
		await key(second, 'ArrowDown');
		expect(second.getAttribute('aria-activedescendant')).not.toBe(firstId);
	});

	it('styles a row that is both selected and active the same way on every render', async () => {
		const trigger = await mountSelect();
		await key(trigger, 'ArrowDown');
		const selectedAndActive = options()[1];
		expect(selectedAndActive.className).toContain('bg-surface-3');
		expect(selectedAndActive.className).not.toContain('bg-signal/10');

		await key(trigger, 'ArrowDown');
		expect(options()[1].className).toContain('bg-signal/10');
		expect(options()[1].className).not.toContain('bg-surface-3');
	});

	it('scrolls the active option into view', async () => {
		const calls: Element[] = [];
		const original = Element.prototype.scrollIntoView;
		Element.prototype.scrollIntoView = function (this: Element) {
			calls.push(this);
		};
		try {
			const trigger = await mountSelect();
			await key(trigger, 'ArrowDown');
			await key(trigger, 'ArrowDown');
			expect(calls[calls.length - 1]).toBe(options()[2]);
		} finally {
			Element.prototype.scrollIntoView = original;
		}
	});
});
