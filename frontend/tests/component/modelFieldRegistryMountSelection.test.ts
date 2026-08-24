// @vitest-environment jsdom
//
// A model picker mounted through `plugin-api/componentRegistry` (plugin pages,
// e.g. the spritesheet editor's matting picker) instead of by the form system.
// The registry uses svelte's `mount()` into a plain div, and the picker's
// dropdown is `use:portal`'d to <body> - so the clicked row is NOT inside the
// mount container. This drives the real ModelField + ModelBrowserPanel +
// ModelResultRow chain and asserts a row click reaches `onChange`, which is
// exactly the hop a stubbed picker can never cover.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getModelById: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		// Reached via the auth store, which the details modal pulls in.
		setOnAuthExpired: vi.fn(),
		getToken: vi.fn(() => null),
		getBaseURL: vi.fn(() => 'http://localhost')
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelField } = await import('$lib/components/form-fields/ModelField.svelte');
const { registerComponent, getRegistry } = await import('$lib/plugin-api/componentRegistry');

const MODEL = {
	id: 'MDL01K7',
	filename: 'BiRefNet-general.safetensors',
	file_path: 'detection_segm/BiRefNet-general.safetensors',
	model_type: 'detection_segm',
	name: 'BiRefNet general'
};

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('ModelField mounted through the plugin component registry', () => {
	let host: HTMLDivElement;
	let instance: unknown;
	let onChange: ReturnType<typeof vi.fn>;
	let registryUpdate: (props: Record<string, unknown>) => void;

	beforeEach(() => {
		vi.mocked(api.getModels).mockResolvedValue({
			success: true,
			data: { models: [MODEL], total: 1, availability_indexed: true }
		} as never);
		vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
		// fetchSelectedModel reads `response.data.model` for a `model:<id>` ref.
		vi.mocked(api.getModelById).mockResolvedValue({
			success: true,
			data: { model: MODEL }
		} as never);

		onChange = vi.fn();

		// Mirrors componentRegistry.mount: svelte `mount()` into a nested div
		// that a plugin page owns, NOT the app root.
		const pluginTree = document.createElement('div');
		pluginTree.className = 'sse';
		host = document.createElement('div');
		pluginTree.appendChild(host);
		document.body.appendChild(pluginTree);

		registerComponent('ModelField', ModelField);
		const entry = getRegistry().ModelField;
		instance = entry.mount(host, {
			name: 'matting_model',
			config: {
				title: 'Matting model',
				configuration: {
					model_type: 'detection_segm',
					placeholder: 'Select BiRefNet-general.safetensors...'
				}
			},
			value: '',
			onChange
		});
		registryUpdate = (props) => entry.update?.(instance, props);
	});

	afterEach(() => {
		try {
			getRegistry().ModelField.unmount(instance);
		} catch {
			/* already torn down */
		}
		document.body.innerHTML = '';
		vi.clearAllMocks();
	});

	it('reports the picked model through onChange when a row is clicked', async () => {
		const input = host.querySelector<HTMLInputElement>('input[type="text"]');
		expect(input, 'search input rendered').toBeTruthy();

		input!.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
		await flush(50);

		// The dropdown is portalled to <body>, outside the mount container.
		const row = document.body.querySelector<HTMLElement>('div[role="button"]');
		expect(row, 'a model row rendered in the portalled dropdown').toBeTruthy();

		// A real pointer interaction: pointerdown (which the window-level
		// close handler sees first) then click.
		row!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		await flush(0);
		row!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await flush(20);

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange.mock.calls[0][0]).toBe('matting_model');
		expect(onChange.mock.calls[0][1]).toMatchObject({ modelPath: `model:${MODEL.id}` });
	});

	it('keeps the picked model on screen once the value is fed back', async () => {
		// ModelField is a CONTROLLED field: its display state is derived from the
		// `value` prop, and the block that clears `selectedModelData`/`searchQuery`
		// when `modelPath` is empty re-runs as soon as the selection touches them.
		// A form parent writes the new value back, so it sticks; a mount that
		// cannot push props leaves `value` at '' and the selection is wiped
		// immediately - the model appears unclickable.
		const input = host.querySelector<HTMLInputElement>('input[type="text"]');
		input!.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
		await flush(50);

		const row = document.body.querySelector<HTMLElement>('div[role="button"]');
		row!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		row!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await flush(20);

		// Close the dropdown the way a real click-away does, so the "not typing,
		// not focused" guards that protect the display no longer apply.
		input!.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		await flush(20);

		// Feed the value back exactly as a form parent (and now the registry's
		// prop-update path) does.
		const picked = onChange.mock.calls[0][1];
		registryUpdate({ value: picked });
		await flush(30);

		// The field swaps the search input for a selected-model card, gated on
		// `selectedModelData && modelPath` - and `modelPath` comes from `value`,
		// so the card is unreachable unless the value is fed back.
		expect(host.textContent, 'selected-model card shows the picked model').toContain(
			'BiRefNet general'
		);
		expect(
			host.querySelector('input[type="text"]'),
			'search input replaced by the selection'
		).toBeNull();
	});
});
