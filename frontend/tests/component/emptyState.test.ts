import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';
import EmptyState from '../../src/lib/components/ui/EmptyState.svelte';

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof mount> | null = null;

function render(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(EmptyState, { target, props: props as never });
	flushSync();
	return target;
}

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('EmptyState', () => {
	it('renders the icon tile, the mono eyebrow, the description and the actions', () => {
		const actions = createRawSnippet(() => ({ render: () => '<button data-action>Do it</button>' }));
		const root = render({ title: 'No models available', description: 'Install one to begin.', icon: 'model', actions });
		const title = root.querySelector('[data-empty-state-title]')!;
		expect(title.textContent).toBe('No models available');
		expect(title.className).toContain('font-mono');
		expect(title.className).toContain('uppercase');
		expect(root.querySelector('[data-empty-state-description]')!.textContent).toBe('Install one to begin.');
		expect(root.querySelector('svg')).not.toBeNull();
		expect(root.querySelector('[data-action]')!.textContent).toBe('Do it');
		expect(root.querySelector('.dot-grid')).not.toBeNull();
		expect(root.querySelector('.w-16.h-16')).not.toBeNull();
	});

	it('omits the description and the actions row when none are given', () => {
		const root = render({ title: 'Nothing' });
		expect(root.querySelector('[data-empty-state-description]')).toBeNull();
		expect(root.querySelector('[data-action]')).toBeNull();
	});

	it('keeps the same anatomy at a smaller scale when compact', () => {
		const root = render({ title: 'Nothing', description: 'Small.', compact: true });
		expect(root.querySelector('[data-empty-state]')!.getAttribute('data-empty-state')).toBe('compact');
		expect(root.querySelector('.w-12.h-12')).not.toBeNull();
		expect(root.querySelector('.w-16.h-16')).toBeNull();
		expect(root.querySelector('[data-empty-state-title]')).not.toBeNull();
	});

	it('tints the eyebrow with the danger tone', () => {
		const root = render({ title: 'Broken', tone: 'danger' });
		expect(root.querySelector('[data-empty-state-title]')!.className).toContain('text-danger');
	});
});
