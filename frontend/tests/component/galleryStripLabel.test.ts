// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import GalleryStrip from '$lib/components/workbench/GalleryStrip.svelte';

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
});

function render(batchImages: { url: string; label?: string | null }[]) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(GalleryStrip, { target, props: { batchImages } });
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

describe('GalleryStrip output label', () => {
	it('shows a caption chip only on the labelled thumbnail', () => {
		const target = render([{ url: '/a.png' }, { url: '/b.png', label: 'Guide: Pose' }]);

		const chips = target.querySelectorAll('[data-output-label]');
		expect(chips).toHaveLength(1);
		expect(chips[0].textContent).toContain('Guide: Pose');
		expect(chips[0].closest('[role="button"]')?.querySelector('img')?.getAttribute('src')).toBe('/b.png');
	});

	it('renders no chip when no image has a label', () => {
		const target = render([{ url: '/a.png', label: null }, { url: '/b.png' }]);
		expect(target.querySelectorAll('[data-output-label]')).toHaveLength(0);
	});
});
