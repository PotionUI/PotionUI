// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: ResourceChip } = await import('$lib/components/chat/ResourceChip.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ResourceChip as never, target, props });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.useRealTimers();
});

describe('ResourceChip', () => {
	it('carries no native title and shows the uri as a real tooltip on hover', async () => {
		vi.useFakeTimers();
		mounted = mount({ uri: 'preset://sdxl/base', label: 'base' });

		const chip = mounted.target.querySelector('.inline-chip')!;
		expect(chip.hasAttribute('title')).toBe(false);

		const labelSpan = mounted.target.querySelector('.font-mono')!;
		labelSpan.parentElement!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(250);

		const tooltip = Array.from(document.body.querySelectorAll('div')).find(
			(el) => el.textContent?.trim() === 'preset://sdxl/base' && el.className.includes('fixed')
		);
		expect(tooltip, 'Tooltip did not render into the DOM on hover').toBeTruthy();
	});

	it('the remove button has no native title but keeps its accessible name and click behavior', async () => {
		vi.useFakeTimers();
		const onremove = vi.fn();
		mounted = mount({ uri: 'preset://sdxl/base', label: 'base', onremove });

		const removeButton = mounted.target.querySelector('button')!;
		expect(removeButton.hasAttribute('title')).toBe(false);

		removeButton.parentElement!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(250);

		const tooltip = Array.from(document.body.querySelectorAll('div')).find(
			(el) => el.textContent?.trim() === 'Remove resource' && el.className.includes('fixed')
		);
		expect(tooltip, 'Tooltip did not render into the DOM on hover').toBeTruthy();

		removeButton.click();
		expect(onremove).toHaveBeenCalledTimes(1);
	});
});
