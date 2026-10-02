// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

registerFieldComponent('string', { component: TextInput });
registerFieldComponent('row', { component: RowField });

const SCHEMA = {
	properties: {
		media_inputs: {
			type: 'row',
			children: [
				{ name: 'prompt', type: 'string', title: 'Prompt', default: '' },
				{ name: 'media_inputs', type: 'mixed-media-stub', title: 'References', merge_from: ['old_clips', 'old_tracks'] }
			]
		}
	}
};

const image = { path: 'uploads/a.png', type: 'image' };
const video = { path: 'uploads/v.mp4', type: 'video' };
const audio = { path: 'uploads/t.wav', type: 'audio' };

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let destroy: (() => void) | undefined;

beforeEach(() => {
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: SCHEMA }
	} as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

function mount(initialData: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const log: Array<Record<string, unknown>> = [];
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: {
			presetId: `native-${Math.random()}`,
			mode: 'refs',
			initialData,
			onFormDataChange: (data: Record<string, unknown>) => log.push(data)
		}
	}) as unknown as { $set: (props: Record<string, unknown>) => void; $destroy: () => void };
	destroy = () => {
		form.$destroy();
		target.remove();
	};
	return { form, log };
}

describe('DynamicForm merge_from', () => {
	it('folds saved old keys into the field on first load and drops them', async () => {
		const { log } = mount({ media_inputs: [image], old_clips: [video], old_tracks: [audio] });
		await settle();
		const published = log[log.length - 1];
		expect(published.media_inputs).toEqual([image, video, audio]);
		expect('old_clips' in published).toBe(false);
		expect('old_tracks' in published).toBe(false);
	});

	it('folds old keys from a later session load', async () => {
		const { form, log } = mount({ media_inputs: [image] });
		await settle();
		expect(log[log.length - 1].media_inputs).toEqual([image]);
		form.$set({ initialData: { media_inputs: [], old_tracks: [audio] } });
		await settle();
		const published = log[log.length - 1];
		expect(published.media_inputs).toEqual([audio]);
		expect('old_tracks' in published).toBe(false);
	});
});
