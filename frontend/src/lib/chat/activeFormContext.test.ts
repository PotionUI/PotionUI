import { describe, expect, it } from 'vitest';
import {
	chatFormSessionId,
	isGeneratePageContext,
	isGenerationPresetContext,
	resolveChatContextTab
} from './activeFormContext';

describe('isGeneratePageContext', () => {
	it('is true for the generation mode (the Generate page + home route)', () => {
		expect(isGeneratePageContext('generation')).toBe(true);
	});

	it('is false for any other page mode', () => {
		expect(isGeneratePageContext('history')).toBe(false);
		expect(isGeneratePageContext('lora-dataset')).toBe(false);
	});

	it('is false when the page mode has not resolved yet', () => {
		expect(isGeneratePageContext(null)).toBe(false);
	});
});

describe('isGenerationPresetContext', () => {
	it('is true for a generation-mode session', () => {
		expect(isGenerationPresetContext('generation')).toBe(true);
	});

	it('is false for a plugin-mode session', () => {
		expect(isGenerationPresetContext('lora-dataset')).toBe(false);
	});
});

describe('chat form session id', () => {
	const active = { id: 'tab-active', selectedSessionId: 'sess-active' };
	const pinned = { id: 'tab-pinned', selectedSessionId: 'sess-pinned' };
	const unsaved = { id: 'tab-unsaved', selectedSessionId: null };
	const tabs = [active, pinned, unsaved];

	it('follows the pinned tab over the active one', () => {
		const tab = resolveChatContextTab(tabs, 'tab-pinned', active);
		expect(chatFormSessionId(tab, 'generation')).toBe('sess-pinned');
	});

	it('follows the active tab when nothing is pinned', () => {
		const tab = resolveChatContextTab(tabs, null, active);
		expect(chatFormSessionId(tab, 'generation')).toBe('sess-active');
	});

	it('falls back to the active tab when the pinned tab is gone', () => {
		const tab = resolveChatContextTab(tabs, 'tab-closed', active);
		expect(chatFormSessionId(tab, 'generation')).toBe('sess-active');
	});

	it('is null for an unsaved tab', () => {
		const tab = resolveChatContextTab(tabs, 'tab-unsaved', active);
		expect(chatFormSessionId(tab, 'generation')).toBeNull();
	});

	it('is null for a plugin-mode chat session', () => {
		const tab = resolveChatContextTab(tabs, 'tab-pinned', active);
		expect(chatFormSessionId(tab, 'lora-dataset')).toBeNull();
	});
});
