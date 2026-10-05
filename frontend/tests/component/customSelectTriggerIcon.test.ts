// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import { iconPaths } from '$lib/utils/IconLibrary';

const { default: CustomSelect } = await import('../../src/lib/components/CustomSelect.svelte');

const OPTIONS = [
	{ value: 'a', label: 'Alpha', icon: 'image' },
	{ value: 'b', label: 'Beta', icon: 'video' }
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

function hasGlyph(root: ParentNode, name: string) {
	const d = iconPaths[name];
	const first = Array.isArray(d) ? d[0] : d;
	return Array.from(root.querySelectorAll('svg path')).some((p) => p.getAttribute('d') === first);
}

async function mountSelect(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(CustomSelect as never, { target, props: { options: OPTIONS, value: 'a', ...props } } as never);
	flushSync();
	return target.querySelector('button[aria-haspopup="listbox"]') as HTMLElement;
}

async function open(trigger: HTMLElement) {
	trigger.click();
	await tick();
	flushSync();
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
}

describe('CustomSelect triggerIcon', () => {
	it('shows the selected option icon on the trigger by default', async () => {
		const trigger = await mountSelect();
		expect(hasGlyph(trigger, 'image')).toBe(true);
	});

	it('keeps icons in the menu but off the trigger when triggerIcon is false', async () => {
		const trigger = await mountSelect({ triggerIcon: false });
		expect(hasGlyph(trigger, 'image')).toBe(false);
		const [alpha, beta] = await open(trigger);
		expect(hasGlyph(alpha, 'image')).toBe(true);
		expect(hasGlyph(beta, 'video')).toBe(true);
	});
});
