import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { hasIcon } from '$lib/utils/IconLibrary';

const { getModeLabels } = vi.hoisted(() => ({ getModeLabels: vi.fn() }));

vi.mock('$lib/services/api/index', () => ({ api: { getModeLabels } }));

async function freshModule() {
	vi.resetModules();
	return import('./modeLabel.svelte');
}

beforeEach(() => {
	getModeLabels.mockReset();
});

describe('fallbackModeLabel', () => {
	it('title-cases the key with underscores and dashes as spaces', async () => {
		const { fallbackModeLabel } = await freshModule();
		expect(fallbackModeLabel('face_swap')).toBe('Face Swap');
		expect(fallbackModeLabel('style-transfer')).toBe('Style Transfer');
		expect(fallbackModeLabel('relight')).toBe('Relight');
	});
});

describe('modeLabel', () => {
	it('prefers the label the server served for this preset', async () => {
		const { modeLabel, setModeLabels } = await freshModule();
		setModeLabels({ refs: 'References' });
		expect(modeLabel('refs', 'References to Video')).toBe('References to Video');
		expect(modeLabel('refs', '  ')).toBe('References');
	});

	it('resolves stored keys through the served map, case-insensitively', async () => {
		const { modeLabel, setModeLabels } = await freshModule();
		setModeLabels({ txt2img: 'Text to Image', img2video: 'Image to Video' });
		expect(modeLabel('txt2img')).toBe('Text to Image');
		expect(modeLabel('IMG2VIDEO')).toBe('Image to Video');
	});

	it('falls back to title case for keys the map does not name', async () => {
		const { modeLabel, setModeLabels } = await freshModule();
		setModeLabels({ txt2img: 'Text to Image' });
		expect(modeLabel('face_swap')).toBe('Face Swap');
	});

	it('is empty for a missing key', async () => {
		const { modeLabel } = await freshModule();
		expect(modeLabel(null)).toBe('');
		expect(modeLabel(undefined)).toBe('');
		expect(modeLabel('')).toBe('');
	});
});

describe('loadModeLabels', () => {
	it('fetches the map once and shares one request', async () => {
		const { loadModeLabels, modeLabel } = await freshModule();
		getModeLabels.mockResolvedValue({ success: true, data: { labels: { txt2img: 'Text to Image' } } });
		expect(modeLabel('txt2img')).toBe('Txt2img');
		await Promise.all([loadModeLabels(), loadModeLabels()]);
		await loadModeLabels();
		expect(getModeLabels).toHaveBeenCalledTimes(1);
		expect(modeLabel('txt2img')).toBe('Text to Image');
	});

	it('retries after a failed request', async () => {
		const { loadModeLabels, modeLabel } = await freshModule();
		getModeLabels.mockRejectedValueOnce(new Error('offline'));
		await loadModeLabels();
		expect(modeLabel('txt2img')).toBe('Txt2img');
		getModeLabels.mockResolvedValueOnce({ success: true, data: { labels: { txt2img: 'Text to Image' } } });
		await loadModeLabels();
		expect(getModeLabels).toHaveBeenCalledTimes(2);
		expect(modeLabel('txt2img')).toBe('Text to Image');
	});
});

describe('mode icons', () => {
	it('every icon in the shared mode map exists in the Icon set', () => {
		const map: Record<string, { label: string; icon?: string }> = JSON.parse(
			readFileSync(join(__dirname, '../../../../src/features/presets/mode_names.json'), 'utf8')
		);
		const icons = Object.values(map).flatMap((entry) => (entry.icon ? [entry.icon] : []));
		expect(icons.length).toBeGreaterThan(20);
		for (const name of icons) expect(hasIcon(name), name).toBe(true);
	});
});
