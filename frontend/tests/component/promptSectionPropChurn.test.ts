// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync, tick } from 'svelte';
import { propReadProbe } from './fixtures/propReadProbe';

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

vi.mock('../../src/lib/components/SegmentedPromptEditor.svelte', async () => ({
	default: (await import('./fixtures/PropReadProbe.svelte')).default
}));

const { default: PromptSection } = await import('../../src/routes/generate/components/PromptSection.svelte');
const { createClassComponent } = await import('svelte/legacy');

const WATCHED = [
	'variables',
	'variableRolls',
	'resourceFieldValues',
	'resourceFieldLabels',
	'promptResources',
	'promptSyntax',
	'activeTriggerWords',
	'negativeSegments',
	'onOpenStyles'
];

function segment(id: string, content: string) {
	return { id, content, type: 'content', chips: {}, enabled: true };
}

const tabHandlers = {
	handlePromptChange: () => {},
	handlePromptSegmentsChange: () => {},
	handleNegativePromptChange: () => {},
	handleNegativePromptSegmentsChange: () => {},
	handlePromptTabsChange: () => {},
	handleActivePromptTabChange: () => {}
};

async function settle() {
	for (let i = 0; i < 4; i++) {
		await tick();
		await Promise.resolve();
	}
	flushSync();
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('PromptSection under prompt keystrokes', () => {
	it('keeps every non-segment prop of the segment editor stable while the prompt changes', async () => {
		propReadProbe.watch(WATCHED);
		const target = document.createElement('div');
		document.body.appendChild(target);
		const tab = {
			id: 'tab-section-churn',
			name: 'Churn',
			selectedPreset: 'preset-a',
			selectedMode: 'txt2img',
			selectedVariant: null,
			prompt: '',
			negativePrompt: '',
			promptSegments: [segment('s1', '')],
			negativePromptSegments: [],
			formData: { steps: 20 },
			variables: { mood: { mode: 'fixed', options: ['calm'] } }
		};
		const component = createClassComponent({
			component: PromptSection as never,
			target,
			props: { tab, tabHandlers, promptRelayActive: false, numPrompts: 1 }
		});
		await settle();
		expect(propReadProbe.runs.promptSyntax).toBeGreaterThanOrEqual(1);
		propReadProbe.reset();

		for (const text of ['a', 'ab', 'abc']) {
			component.$set({ tab: { ...tab, prompt: text, promptSegments: [segment('s1', text)] } });
			await settle();
		}
		expect(propReadProbe.runs).toEqual({});

		component.$set({ tab: { ...tab, variables: { mood: { mode: 'fixed', options: ['wild'] } } } });
		await settle();
		expect(propReadProbe.runs).toEqual({ variables: 1 });
		component.$destroy();
	});
});
