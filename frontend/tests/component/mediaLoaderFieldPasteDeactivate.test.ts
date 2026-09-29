// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		editMediaItem: vi.fn(),
		extractMediaFrame: vi.fn(),
		listLibraryItems: vi.fn().mockResolvedValue({ success: true, data: { items: [], total: 0 } }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

const { default: MediaLoaderField } = await import('$lib/components/form-fields/MediaLoaderField.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function pasteHint(container: HTMLElement): string | null {
	const hint = Array.from(container.querySelectorAll('p')).find((el) =>
		/Paste armed|paste from clipboard/.test(el.textContent ?? '')
	);
	return hint?.textContent?.trim() ?? null;
}

function mountInsideModal() {
	const modal = document.createElement('div');
	modal.setAttribute('role', 'dialog');
	modal.className = 'fixed z-overlay';
	document.body.appendChild(modal);

	const target = document.createElement('div');
	modal.appendChild(target);

	const otherField = document.createElement('input');
	otherField.type = 'text';
	modal.appendChild(otherField);

	const component = createClassComponent({
		component: MediaLoaderField as never,
		target,
		props: {
			name: 'reference',
			config: {},
			value: null,
			onChange: vi.fn()
		}
	});

	return { modal, target, otherField, component };
}

describe('MediaLoaderField paste-mode deactivation', () => {
	it('turns off on a mousedown elsewhere inside the same modal', async () => {
		const { target, otherField, component } = mountInsideModal();
		await tick();

		const uploadArea = target.querySelector('[role="button"]') as HTMLElement;
		expect(uploadArea).toBeTruthy();
		uploadArea.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await tick();
		expect(pasteHint(target)).toContain('Paste armed');

		otherField.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
		await tick();
		expect(pasteHint(target)).toContain('or paste from clipboard');

		component.$destroy();
	});
});
