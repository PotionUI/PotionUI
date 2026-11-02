import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { insertTriggerIntoActivePrompt } from './promptInsertion';

describe('insertTriggerIntoActivePrompt', () => {
	beforeEach(() => tabsStore.reset());

	function activeTabId() {
		return get(tabsStore).activeTabId;
	}

	it('inserts the trigger into an empty prompt', () => {
		const result = insertTriggerIntoActivePrompt('masterpiece');
		expect(result).toBe('inserted');

		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === activeTabId())!;
		expect(tab.prompt).toBe('masterpiece');
	});

	it('appends the trigger to a non-empty prompt', () => {
		const id = activeTabId();
		tabsStore.updateTab(id, {
			promptSegments: [{ id: 'seg-1', content: 'a cat', type: 'content' }],
			prompt: 'a cat'
		});

		const result = insertTriggerIntoActivePrompt('best quality');
		expect(result).toBe('inserted');

		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
		expect(tab.prompt).toBe('a cat, best quality');
	});

	it('detects duplicates case-insensitively', () => {
		const id = activeTabId();
		tabsStore.updateTab(id, {
			promptSegments: [{ id: 'seg-1', content: 'a cat, Masterpiece', type: 'content' }],
			prompt: 'a cat, Masterpiece'
		});

		const result = insertTriggerIntoActivePrompt('masterpiece');
		expect(result).toBe('duplicate');

		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
		expect(tab.prompt).toBe('a cat, Masterpiece');
	});

	it('keeps targeting the active tab when asked to switch to a tab that does not exist', () => {
		tabsStore.reset();
		const id = activeTabId();
		tabsStore.setActiveTab('does-not-exist');

		expect(activeTabId()).toBe(id);
		expect(insertTriggerIntoActivePrompt('masterpiece')).toBe('inserted');
		expect(generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!.prompt).toBe('masterpiece');
	});

	it('inserts into the last generation tab while a tool tab is up, leaving the tool tab alone', () => {
		tabsStore.reset();
		const generationId = activeTabId();
		const toolId = tabsStore.openToolTab('image', { path: '/m/dusk.png' });
		const toolBefore = get(tabsStore).tabs.find((t) => t.id === toolId);

		expect(insertTriggerIntoActivePrompt('masterpiece')).toBe('inserted');

		const state = get(tabsStore);
		expect(state.activeTabId).toBe(toolId);
		expect(state.tabs.find((t) => t.id === toolId)).toBe(toolBefore);
		expect(generationTabs(state.tabs).find((t) => t.id === generationId)!.prompt).toBe('masterpiece');
	});

	it('inserts into the active prompt of a multi-prompt tab and leaves the others alone', () => {
		const id = activeTabId();
		const first = { promptSegments: [{ id: 'a', content: 'a cat', type: 'content' as const }], negativePromptSegments: [], prompt: 'a cat', negativePrompt: '' };
		const second = { promptSegments: [{ id: 'b', content: 'a dog', type: 'content' as const }], negativePromptSegments: [], prompt: 'a dog', negativePrompt: '' };
		tabsStore.updateTab(id, {
			promptTabs: [first, second],
			activePromptTab: 1,
			promptSegments: second.promptSegments,
			prompt: second.prompt
		});

		const result = insertTriggerIntoActivePrompt('masterpiece');

		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
		expect(result).toBe('inserted');
		expect(tab.promptTabs![1].prompt).toBe('a dog, masterpiece');
		expect(tab.promptTabs![0]).toEqual(first);
	});

	it('returns unavailable for prompt-relay mode tabs', () => {
		const id = activeTabId();
		tabsStore.updateTab(id, {
			promptRelay: {
				global_prompt: '',
				timeline: { duration: 0, fps: 24, segments: [] }
			}
		});

		const result = insertTriggerIntoActivePrompt('masterpiece');
		expect(result).toBe('unavailable');
	});
});
