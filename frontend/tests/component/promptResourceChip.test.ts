// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const { default: PromptResourceChip } = await import('$lib/components/PromptResourceChip.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PromptResourceChip as never, target, props });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

const spec: PromptResourceSpec = { field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' };

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.useRealTimers();
});

describe('PromptResourceChip', () => {
	it('renders the resolved handle label and thumbnail for a live item', () => {
		mounted = mount({
			field: 'references',
			itemKey: 'a.png',
			spec,
			position: 2,
			item: { url: '/api/media/uploads/a.png', name: 'a.png' }
		});
		expect(mounted.target.textContent).toContain('Picture 2');
		const img = mounted.target.querySelector('img');
		expect(img?.getAttribute('src')).toBe('/api/media/uploads/a.png');
		expect(mounted.target.querySelector('.resource-chip')?.className).not.toContain('text-danger');
	});

	it('has no native title anywhere on the chip', () => {
		mounted = mount({
			field: 'references',
			itemKey: 'a.png',
			spec,
			position: 1,
			item: { url: '/a.png', name: 'a.png' }
		});
		const withTitle = mounted.target.querySelectorAll('[title]');
		expect(withTitle.length).toBe(0);
	});

	it('renders a danger chip with a remove button when the item is no longer in the field', () => {
		const onRemove = vi.fn();
		mounted = mount({
			field: 'references',
			itemKey: 'gone.png',
			spec,
			position: null,
			fieldLabel: 'References',
			onRemove
		});
		const chip = mounted.target.querySelector('.resource-chip')!;
		expect(chip.className).toContain('text-danger');
		const removeButton = mounted.target.querySelector('button[aria-label="Remove reference"]');
		expect(removeButton).toBeTruthy();
		(removeButton as HTMLButtonElement).dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		expect(onRemove).toHaveBeenCalledTimes(1);
	});

	it('renders a danger chip with no remove button when disabled', () => {
		mounted = mount({
			field: 'references',
			itemKey: 'gone.png',
			spec,
			position: null,
			disabled: true
		});
		expect(mounted.target.querySelector('button[aria-label="Remove reference"]')).toBeNull();
	});

	it('is dangling when the field has no mapped spec at all', () => {
		mounted = mount({
			field: 'unmapped_field',
			itemKey: 'a.png',
			spec: null,
			position: null
		});
		const chip = mounted.target.querySelector('.resource-chip')!;
		expect(chip.className).toContain('text-danger');
		expect(mounted.target.textContent).toContain('unmapped_field');
	});

	it('shows the tooltip text on hover without a native title', async () => {
		vi.useFakeTimers();
		mounted = mount({
			field: 'references',
			itemKey: 'gone.png',
			spec,
			position: null,
			fieldLabel: 'References'
		});
		const trigger = mounted.target.querySelector('.resource-chip > div > div')!;
		trigger.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(250);
		const tooltip = Array.from(document.body.querySelectorAll('div')).find(
			(el) => el.textContent?.trim() === 'Removed from References' && el.className.includes('fixed')
		);
		expect(tooltip, 'Tooltip did not render into the DOM on hover').toBeTruthy();
	});
});
