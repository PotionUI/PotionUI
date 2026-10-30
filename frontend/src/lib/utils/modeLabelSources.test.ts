import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';
import { modeLabelIn, setModeLabels } from './modeLabel.svelte';
import { presetModeOptions } from './presetModeOptions';

const REPO = join(process.cwd(), '..');
const PRESETS = join(REPO, 'content', 'presets', 'marketplace');
const GENERIC = JSON.parse(readFileSync(join(REPO, 'src', 'features', 'presets', 'mode_names.json'), 'utf-8')) as Record<
	string,
	{ label: string }
>;

function modesOf(preset: string): Array<{ name: string; label: string }> {
	const manifest = readFileSync(join(PRESETS, preset, 'preset.yml'), 'utf-8');
	const block = /^modes:\s*\n((?:\s+-\s+.+\n)+)/m.exec(manifest)?.[1] ?? '';
	const names = [...block.matchAll(/-\s+"?([A-Za-z0-9_-]+)"?/g)].map((match) => match[1]);
	return names.map((name) => {
		const form = readFileSync(join(PRESETS, preset, 'modes', name, 'form.yml'), 'utf-8');
		const declared = /^mode_label:\s*"([^"]+)"/m.exec(form)?.[1];
		return { name, label: declared ?? GENERIC[name]?.label ?? name };
	});
}

beforeAll(() => {
	setModeLabels(Object.fromEntries(Object.entries(GENERIC).map(([key, entry]) => [key, entry.label])));
});

describe.each(['LTX-2.5', 'MiniMax-H3', 'QwenImage-2.1'])('%s mode labels', (preset) => {
	const modes = modesOf(preset);

	it('reads the preset modes', () => {
		expect(modes.length).toBeGreaterThan(2);
	});

	it('shows the same label in the dock as in the mode picker for every mode', () => {
		const picker = new Map(
			presetModeOptions(modes.map((mode) => ({ id: mode.name, label: mode.label }))).map((option) => [option.value, option.label])
		);
		for (const mode of modes) {
			expect(modeLabelIn(modes, mode.name), mode.name).toBe(picker.get(mode.name));
			expect(modeLabelIn(modes, mode.name), mode.name).toBe(mode.label);
		}
	});
});

describe('modeLabelIn', () => {
	it('prefers the preset label over the generic one, for entries keyed by name or id', () => {
		setModeLabels({ extend: 'Extend', day_to_night: 'Day to Night' });

		expect(modeLabelIn([{ name: 'extend', label: 'Extend video' }], 'extend')).toBe('Extend video');
		expect(modeLabelIn([{ id: 'day_to_night', label: 'Day to night' }], 'day_to_night')).toBe('Day to night');
		expect(modeLabelIn([], 'extend')).toBe('Extend');
	});
});
