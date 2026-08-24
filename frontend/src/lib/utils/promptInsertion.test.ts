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

		const tab = get(tabsStore).tabs.find((t) => t.id === activeTabId())!;
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

		const tab = get(tabsStore).tabs.find((t) => t.id === id)!;
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

		const tab = get(tabsStore).tabs.find((t) => t.id === id)!;
		expect(tab.prompt).toBe('a cat, Masterpiece');
	});

	it('returns unavailable when there is no active tab', () => {
		tabsStore.reset();
		tabsStore.setActiveTab('does-not-exist');

		const result = insertTriggerIntoActivePrompt('masterpiece');
		expect(result).toBe('unavailable');
	});

	it('returns unavailable for multi-prompt tabs', () => {
		const id = activeTabId();
		tabsStore.updateTab(id, {
			promptTabs: [
				{ promptSegments: [], negativePromptSegments: [], prompt: '', negativePrompt: '' },
				{ promptSegments: [], negativePromptSegments: [], prompt: '', negativePrompt: '' }
			]
		});

		const result = insertTriggerIntoActivePrompt('masterpiece');
		expect(result).toBe('unavailable');
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
