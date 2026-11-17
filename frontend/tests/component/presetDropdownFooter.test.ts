import { describe, it, expect, afterEach, vi } from 'vitest';
import { createRawSnippet, mount, unmount } from 'svelte';
import type { PresetInfo } from '$lib/types/api';
import { settle } from './helpers/settle';

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn({ user: { account_type: 'USER' } }), () => {}) }
}));

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetPickerState: vi.fn(async () => ({ success: true, data: { pinned: [], recent: [] } })), getClient: () => ({ get: vi.fn() }) }
}));

vi.mock('$lib/utils/presetHardware', async (orig) => ({
	...(await orig<typeof import('$lib/utils/presetHardware')>()),
	fetchDetectedVramGb: vi.fn().mockResolvedValue(16)
}));

const { default: PresetDropdown } = await import('$lib/components/preset/PresetDropdown.svelte');

const PRESETS = [{ id: 'sdxl', name: 'SDXL', engine: 'native', version: '1', category: 'image', tags: [], engine_available: true }] as unknown as PresetInfo[];

let instance: ReturnType<typeof mount> | undefined;

afterEach(async () => {
	if (instance) await unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

async function open(footer?: ReturnType<typeof createRawSnippet>) {
	const anchor = document.createElement('button');
	document.body.appendChild(anchor);
	instance = mount(PresetDropdown, { target: document.body, props: { presets: PRESETS, anchor, onSelect: () => undefined, onReload: () => undefined, onClose: () => undefined, ...(footer ? { footer } : {}) } });
	await settle();
}

describe('PresetDropdown footer', () => {
	it('renders nothing extra without a footer', async () => {
		await open();
		expect(document.body.querySelector('[data-testid="preset-list-scroll"]')).not.toBeNull();
		expect(document.body.querySelector('[data-test-footer]')).toBeNull();
	});

	it('renders the footer snippet below the list', async () => {
		await open(createRawSnippet(() => ({ render: () => '<div data-test-footer>footer</div>' })));
		const list = document.body.querySelector('[data-testid="preset-list-scroll"]')!;
		const footer = document.body.querySelector('[data-test-footer]')!;
		expect(footer).not.toBeNull();
		expect(list.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});
});
