// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { default: TrimEditor } = await import('../../src/lib/media/editors/TrimEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: Array<{ component: { $destroy: () => void }; target: HTMLElement }> = [];

afterEach(() => {
	for (const { component, target } of mounted) {
		component.$destroy();
		target.remove();
	}
	mounted = [];
});

function mountTrim() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: TrimEditor as never,
		target,
		props: {
			source: { url: '/clip.mp4', kind: 'video', fileName: 'clip.mp4' },
			onClose: () => {},
			commit: () => {}
		}
	});
	mounted.push({ component, target });
	return target;
}

function clippingAncestors(handle: HTMLElement, rail: HTMLElement): HTMLElement[] {
	const found: HTMLElement[] = [];
	for (let node = handle.parentElement; node && node !== rail.parentElement; node = node.parentElement) {
		if (node.classList.contains('overflow-hidden')) found.push(node);
	}
	return found;
}

describe('trim handles', () => {
	it('are not inside any clipping element of the rail', () => {
		mountTrim();
		const rail = document.querySelector('[role="presentation"]') as HTMLElement;
		expect(rail).not.toBeNull();
		for (const label of ['Trim in point', 'Trim out point']) {
			const handle = document.querySelector(`button[aria-label="${label}"]`) as HTMLElement;
			expect(handle, label).not.toBeNull();
			expect(rail.contains(handle)).toBe(true);
			expect(clippingAncestors(handle, rail), label).toEqual([]);
		}
	});

	it('keeps the tick and dim layers clipped to the rail corners', () => {
		mountTrim();
		const rail = document.querySelector('[role="presentation"]') as HTMLElement;
		const layer = rail.querySelector('.overflow-hidden');
		expect(layer).not.toBeNull();
		expect(layer!.querySelector('button')).toBeNull();
	});
});
