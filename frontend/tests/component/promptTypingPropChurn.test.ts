// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync, tick } from 'svelte';
import { propReadProbe } from './fixtures/propReadProbe';

const parseCalls: string[] = [];

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

vi.mock('../../src/lib/components/chipSegments', async (importOriginal) => {
	const actual = await importOriginal<typeof import('../../src/lib/components/chipSegments')>();
	return {
		...actual,
		parseValueToSegments: (...args: Parameters<typeof actual.parseValueToSegments>) => {
			parseCalls.push(args[0]);
			return actual.parseValueToSegments(...args);
		}
	};
});

vi.mock('../../src/lib/utils/presetPromptResourcesCache', () => {
	const result = {
		specs: [{ field: 'refs', kind: 'image', token: '@', label: 'Reference' }],
		fieldLabels: { refs: 'References' }
	};
	return { getPresetPromptResources: () => Promise.resolve(result) };
});

vi.mock('../../src/lib/utils/presetPromptSyntaxCache', () => {
	const specs = [{ token: '(tag:1.2)', kind: 'weight', pattern: '\\([^()]+:\\d+(?:\\.\\d+)?\\)' }];
	return { getPresetPromptSyntax: () => Promise.resolve(specs) };
});

vi.mock('../../src/lib/stores/promptSegmentActionPins', async () => {
	const { writable } = await import('svelte/store');
	const pins = writable({ ids: [] as string[] });
	return { promptSegmentActionPins: { subscribe: pins.subscribe, init: () => {}, togglePin: () => {} } };
});

vi.mock('../../src/lib/components/DynamicForm.svelte', async () => ({
	default: (await import('./fixtures/PropReadProbe.svelte')).default
}));

const { default: SegmentedPromptEditor } = await import('../../src/lib/components/SegmentedPromptEditor.svelte');
const { default: GenerationFormPane } = await import('../../src/routes/generate/components/GenerationFormPane.svelte');
const { createClassComponent } = await import('svelte/legacy');

function segment(id: string, content: string) {
	return { id, content, type: 'content', chips: {}, enabled: true };
}

function baseTab(overrides: Record<string, unknown> = {}) {
	return {
		id: 'tab-churn',
		name: 'Churn',
		selectedPreset: 'preset-a',
		selectedMode: 'txt2img',
		selectedVariant: null,
		prompt: '',
		negativePrompt: '',
		promptSegments: [segment('s1', 'a lighthouse keeper')],
		negativePromptSegments: [],
		formData: { steps: 20, refs: [] },
		variables: {},
		...overrides
	};
}

async function settle() {
	for (let i = 0; i < 4; i++) {
		await tick();
		await Promise.resolve();
	}
	flushSync();
}

afterEach(() => {
	document.body.innerHTML = '';
	parseCalls.length = 0;
});

describe('typing into one segment', () => {
	it('re-parses only the edited segment, not its unchanged siblings', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const segments = [
			segment('s1', 'first segment text'),
			segment('s2', 'second segment text'),
			segment('s3', 'third segment text')
		];
		const component = createClassComponent({
			component: SegmentedPromptEditor as never,
			target,
			props: { segments }
		});
		await settle();
		parseCalls.length = 0;

		component.$set({ segments: [{ ...segments[0], content: 'first segment text!' }, segments[1], segments[2]] });
		await settle();

		expect(parseCalls).toContain('first segment text!');
		expect(parseCalls).not.toContain('second segment text');
		expect(parseCalls).not.toContain('third segment text');
		component.$destroy();
	});
});

describe('GenerationFormPane under prompt keystrokes', () => {
	it('hands DynamicForm the same props when only the prompt changed', async () => {
		propReadProbe.watch(['initialData', 'fieldErrors', 'sectionCollapsedContext', 'presetId', 'mode']);
		const target = document.createElement('div');
		document.body.appendChild(target);
		const tab = baseTab();
		const component = createClassComponent({
			component: GenerationFormPane as never,
			target,
			props: { tab, onFormDataChange: () => {} }
		});
		await settle();
		propReadProbe.reset();

		for (const text of ['a', 'ab', 'abc']) {
			component.$set({
				tab: { ...tab, prompt: text, promptSegments: [segment('s1', text)] }
			});
			await settle();
		}
		expect(propReadProbe.runs).toEqual({});

		component.$set({ tab: { ...tab, formData: { steps: 30, refs: [] } } });
		await settle();
		expect(propReadProbe.runs.initialData).toBe(1);
		expect(propReadProbe.runs.fieldErrors).toBeUndefined();
		component.$destroy();
	});
});
