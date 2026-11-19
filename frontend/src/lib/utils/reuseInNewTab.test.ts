import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenerationHistoryItem } from '$lib/types/history';

const goto = vi.fn(async () => undefined);
const addTabWithData = vi.fn(() => 'tab-1');

vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...(args as [])) }));
vi.mock('$lib/stores/tabs', () => ({ tabsStore: { addTabWithData: (...args: unknown[]) => addTabWithData(...(args as [])) } }));
vi.mock('$lib/utils/reuseNotice', () => ({ announceReuseConversion: vi.fn() }));

import { reuseInNewTab } from './reuseInNewTab';

function generation(extra: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id: 'g1',
		preset_id: 'preset-a',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_name: null,
		form_data: { prompt: 'a lighthouse', steps: 20 },
		prompt_state: { prompt: 'a lighthouse' },
		seed: 7,
		...extra
	} as unknown as GenerationHistoryItem;
}

describe('reuseInNewTab', () => {
	beforeEach(() => {
		goto.mockClear();
		addTabWithData.mockClear();
	});

	it('opens a Reused tab with the recorded settings and goes to Generate', async () => {
		const done = await reuseInNewTab(generation());

		expect(done).toBe(true);
		expect(addTabWithData).toHaveBeenCalledTimes(1);
		const [name, data] = addTabWithData.mock.calls[0] as unknown as [string, Record<string, unknown>];
		expect(name).toBe('Reused: Krea 2');
		expect(data.selectedPreset).toBe('preset-a');
		expect((data.formData as Record<string, unknown>).seed).toBe(7);
		expect(goto).toHaveBeenCalledWith('/generate');
	});

	it('resolves once navigation has finished, so the caller can close its modal after it', async () => {
		let release: () => void = () => undefined;
		goto.mockImplementationOnce(() => new Promise<undefined>((resolve) => (release = () => resolve(undefined))));
		let settled = false;
		const pending = reuseInNewTab(generation()).then(() => (settled = true));

		await Promise.resolve();
		expect(settled).toBe(false);
		release();
		await pending;
		expect(settled).toBe(true);
	});

	it('does nothing for a generation without a preset', async () => {
		const done = await reuseInNewTab(generation({ preset_id: null }));

		expect(done).toBe(false);
		expect(addTabWithData).not.toHaveBeenCalled();
		expect(goto).not.toHaveBeenCalled();
	});
});
