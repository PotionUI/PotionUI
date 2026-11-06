import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from 'svelte/server';

const created = vi.hoisted(() => ({ count: 0 }));

vi.mock('$lib/session/sessionController', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/session/sessionController')>();
	return {
		...actual,
		createSessionController: (...args: Parameters<typeof actual.createSessionController>) => {
			created.count += 1;
			return actual.createSessionController(...args);
		}
	};
});

import StudioView from './StudioView.svelte';

const tab = {
	id: 't1',
	name: 'Tab 1',
	selectedPreset: 'p1',
	selectedMode: 'txt2img',
	prompt: 'a cat',
	formData: {},
	embedded: false,
	generation: { artifacts: [] }
};

describe('StudioView session controller', () => {
	beforeEach(() => {
		created.count = 0;
	});

	it('mounts one session controller while no sheet is open', () => {
		const out = render(StudioView, {
			props: {
				tab,
				tabHandlers: {},
				presets: [{ id: 'p1', name: 'P', version: '1' }],
				readiness: null,
				isLoading: false,
				isReloadingPreset: false,
				tabModes: [{ name: 'txt2img', label: 'Txt2img', variants: [] }],
				promptRelayActive: false,
				videoDirectorActive: false,
				videoDirectorCaps: null,
				musicDirectorActive: false,
				musicDirectorCaps: null,
				canBatch: false,
				negativePromptSupported: false,
				negativeInert: false,
				promptlessActive: false,
				dynamicFormRefs: {},
				onFormDataChange: () => {},
				onReload: () => {},
				onMoveToWorkbench: () => {}
			} as any
		});
		expect(out.body).toContain('<div');
		expect(created.count).toBe(1);
	});
});
