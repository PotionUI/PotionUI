import { describe, it, expect } from 'vitest';
import { promptTextForTab } from './loraPromptSource';

const tabs = [
	{ id: 'tab-a', prompt: 'a lighthouse at dusk', promptSegments: [] },
	{ id: 'tab-b', prompt: '', promptSegments: [{ id: 's1', content: 'portrait of a knight' }] }
] as unknown as Parameters<typeof promptTextForTab>[0];

describe('promptTextForTab', () => {
	it('reads the prompt of the tab the form belongs to', () => {
		expect(promptTextForTab(tabs, 'tab-a')).toBe('a lighthouse at dusk');
	});

	it('prefers that tab\'s segments over its plain prompt', () => {
		expect(promptTextForTab(tabs, 'tab-b')).toContain('portrait of a knight');
	});

	it('reads nothing for a form that belongs to no tab', () => {
		expect(promptTextForTab(tabs, undefined)).toBe('');
	});

	it('reads nothing for a tab that no longer exists', () => {
		expect(promptTextForTab(tabs, 'tab-gone')).toBe('');
	});
});
