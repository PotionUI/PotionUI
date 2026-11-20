import { describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';

const { default: ImageSourceMenu } = await import('$lib/components/paint/ImageSourceMenu.svelte');

describe('ImageSourceMenu started on one source', () => {
	it('shows its list when no source is given', () => {
		const view = mountComponent(ImageSourceMenu, { mode: 'layer', onPick: () => {}, onClose: () => {} });
		expect(view.target.querySelector('[data-image-source-menu]')).not.toBeNull();
	});

	it('skips the list and opens the file chooser when started on a file', () => {
		const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
		const view = mountComponent(ImageSourceMenu, { mode: 'layer', start: 'file', onPick: () => {}, onClose: () => {} });
		flushSync();
		expect(view.target.querySelector('[data-image-source-menu]')).toBeNull();
		expect(click).toHaveBeenCalledTimes(1);
		click.mockRestore();
	});

	it('closes when the file chooser is cancelled', () => {
		const closed: string[] = [];
		vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
		const view = mountComponent(ImageSourceMenu, { mode: 'layer', start: 'file', onPick: () => {}, onClose: () => closed.push('closed') });
		view.target.querySelector<HTMLInputElement>('[data-image-source-file]')!.dispatchEvent(new Event('cancel'));
		expect(closed).toEqual(['closed']);
		vi.restoreAllMocks();
	});
});
