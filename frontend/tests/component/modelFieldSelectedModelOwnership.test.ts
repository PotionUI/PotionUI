// @vitest-environment jsdom
//
// ModelField.fetchSelectedModel resolved a `model:<id>` reference and wrote
// it into `selectedModelData` with no check that the value it was resolving
// was still the field's current value, and no coalescing of repeat lookups
// for a value that's still pending. Two consequences: (1) switching the
// selection while a lookup for the previous value is in flight lets that
// stale lookup land afterward and briefly show the wrong model, and (2) a
// reactive re-run for the *same* still-unresolved value (a focus/blur toggle,
// which is a dependency of the same reactive block that triggers the lookup)
// fires a brand new lookup instead of reusing the in-flight one.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getPresetModels: vi.fn(),
		getModelById: vi.fn(),
		getTags: vi.fn(),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		// Reached via the auth store, which ModelDetailsModal pulls in.
		setOnAuthExpired: vi.fn(),
		getToken: vi.fn(() => null),
		getBaseURL: vi.fn(() => 'http://localhost')
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: ModelField } = await import('../../src/lib/components/form-fields/ModelField.svelte');
const { createClassComponent } = await import('svelte/legacy');

// Focus opens the dropdown, which mounts ModelBrowserPanel and fetches its
// own list - irrelevant to this file's assertions, just kept quiet.
vi.mocked(api.getModels).mockResolvedValue({
	success: true,
	data: { models: [], total: 0, availability_indexed: true }
} as never);
vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

function modelById(id: string, name: string) {
	return { success: true, data: { model: { id, filename: `${name}.safetensors`, model_type: 'checkpoint', name } } };
}

// Typed explicitly (not inferred from the literal below) so createClassComponent's
// generic Props - and therefore `component.$set(...)` - accepts `preset_id`,
// which only the preset-scope test needs to set.
type MountFieldConfig = {
	title: string;
	configuration: { model_type: string };
	preset_id?: string;
};

function mountField(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const defaultConfig: MountFieldConfig = { title: 'Checkpoint', configuration: { model_type: 'checkpoint' } };
	const component = createClassComponent({
		component: ModelField as never,
		target,
		props: {
			name: 'checkpoint',
			config: defaultConfig,
			value: '',
			onChange: vi.fn(),
			...props
		}
	});
	return { target, component, destroy: () => component.$destroy() };
}

function flush(ms = 0) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

let mounted: ReturnType<typeof mountField> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('ModelField selected-model lookup ownership', () => {
	it('never applies a slow lookup for the previous value once the selection has moved on', async () => {
		// Each id's own deferred, so both stay independently controllable.
		const lookups: Record<string, ReturnType<typeof deferred<unknown>>> = {
			A: deferred<unknown>(),
			B: deferred<unknown>()
		};
		vi.mocked(api.getModelById).mockImplementation((id: string) => lookups[id].promise as never);

		mounted = mountField({ value: 'model:A' });
		await flush(0);
		// Still unresolved - the search input shows, not a selected-model card.
		expect(mounted.target.querySelector('input[type="text"]')).toBeTruthy();

		// Switch to B before A's lookup resolves - this issues B's own lookup.
		mounted.component.$set({ value: 'model:B' });
		await flush(0);

		// A's slow lookup lands now, well after the switch to B.
		lookups.A.resolve(modelById('A', 'Model A'));
		await flush(20);

		// It must never have been applied, even transiently - guarded by
		// comparing the resolved value against the field's current value.
		expect(mounted.target.textContent).not.toContain('Model A');

		lookups.B.resolve(modelById('B', 'Model B'));
		await flush(20);
		expect(mounted.target.textContent).toContain('Model B');
	});

	it('coalesces a reactive re-run for the same still-pending value into the one in-flight lookup', async () => {
		const lookup = deferred<unknown>();
		vi.mocked(api.getModelById).mockReturnValue(lookup.promise as never);

		mounted = mountField({ value: 'model:A' });
		await flush(0);
		expect(vi.mocked(api.getModelById)).toHaveBeenCalledTimes(1);

		// A focus/blur cycle toggles `inputHasFocus`, which is read inside the
		// same reactive block that triggers the lookup - a real re-run for the
		// still-unresolved value, not a synthetic prop change.
		const input = mounted.target.querySelector<HTMLInputElement>('input[type="text"]')!;
		input.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
		await flush(0);
		input.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
		await flush(0);

		expect(vi.mocked(api.getModelById)).toHaveBeenCalledTimes(1);

		lookup.resolve(modelById('A', 'Model A'));
		await flush(20);
		expect(mounted.target.textContent).toContain('Model A');
	});

	it('re-issues the legacy filename lookup when presetId changes, even though the stored path text stays the same', async () => {
		// Both presets happen to have a model with the same filename, so
		// `matchesStoredValue` alone (path/filename match) can't tell the two
		// scopes apart - only the (path, presetId, modelType) key can.
		function presetModelsResponse(name: string) {
			return {
				success: true,
				data: { models: [{ id: name, filename: 'shared-name.safetensors', model_type: 'checkpoint', name }] }
			};
		}
		const lookups: Record<string, ReturnType<typeof deferred<unknown>>> = {
			'preset-1': deferred<unknown>(),
			'preset-2': deferred<unknown>()
		};
		vi.mocked(api.getPresetModels).mockImplementation(
			(presetId: string) => lookups[presetId].promise as never
		);

		mounted = mountField({
			value: 'shared-name.safetensors',
			config: { title: 'Checkpoint', configuration: { model_type: 'checkpoint' }, preset_id: 'preset-1' }
		});
		await flush(0);
		expect(vi.mocked(api.getPresetModels)).toHaveBeenCalledTimes(1);
		expect(vi.mocked(api.getPresetModels).mock.calls[0][0]).toBe('preset-1');

		// Switch presets while preset-1's lookup is still pending - same
		// stored path text, so the old "already resolved?" guard alone would
		// never have scheduled a second lookup.
		mounted.component.$set({
			config: { title: 'Checkpoint', configuration: { model_type: 'checkpoint' }, preset_id: 'preset-2' }
		});
		await flush(0);
		expect(
			vi.mocked(api.getPresetModels),
			'preset-only change re-issues its own lookup'
		).toHaveBeenCalledTimes(2);
		expect(vi.mocked(api.getPresetModels).mock.calls[1][0]).toBe('preset-2');

		// preset-1's slow lookup lands now, well after the switch to preset-2.
		lookups['preset-1'].resolve(presetModelsResponse('model-under-preset-1'));
		await flush(20);
		expect(mounted.target.textContent).not.toContain('model-under-preset-1');

		lookups['preset-2'].resolve(presetModelsResponse('model-under-preset-2'));
		await flush(20);
		expect(mounted.target.textContent).toContain('model-under-preset-2');
	});

	it('discards a lookup result that resolves after the field is destroyed', async () => {
		const lookup = deferred<unknown>();
		vi.mocked(api.getModelById).mockReturnValue(lookup.promise as never);

		mounted = mountField({ value: 'model:A' });
		await flush(0);

		mounted.destroy();
		mounted = undefined;

		// Resolving after teardown must not throw.
		expect(() => lookup.resolve(modelById('A', 'Model A'))).not.toThrow();
		await flush(20);
	});
});
