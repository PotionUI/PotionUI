import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';

const { default: RunReportToc } = await import(
	'../../src/routes/admin/components/generations/RunReportToc.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountComponent(props: ComponentProps<typeof RunReportToc>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RunReportToc, { target, props });
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

const sections = [
	{ id: 'overview', label: 'Overview' },
	{ id: 'outputs', label: 'Outputs' }
];

describe('RunReportToc', () => {
	it('renders one entry per section and marks the active one current', () => {
		mountComponent({ sections, activeId: 'outputs', onSelect: vi.fn() });
		const buttons = Array.from(target.querySelectorAll('button'));
		expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Overview', 'Outputs']);
		expect(buttons[0].getAttribute('aria-current')).toBeNull();
		expect(buttons[1].getAttribute('aria-current')).toBe('true');
	});

	it('calls onSelect with the clicked section id', () => {
		const onSelect = vi.fn();
		mountComponent({ sections, activeId: 'overview', onSelect });
		const buttons = Array.from(target.querySelectorAll('button'));
		buttons[1].click();
		expect(onSelect).toHaveBeenCalledWith('outputs');
	});
});
