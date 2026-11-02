import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { OP_ICONS } from '$lib/tool-tabs/video/videoSteps';
import { getIconPath, hasIcon } from './IconLibrary';

const OPS_DIR = join(process.cwd(), '..', 'src', 'platform', 'media_tools', 'ops');

function declaredIcons(): Array<[string, string]> {
	const found: Array<[string, string]> = [];
	for (const file of readdirSync(OPS_DIR).filter((name) => name.endsWith('.py'))) {
		const match = /^\s*icon="([^"]+)",/m.exec(readFileSync(join(OPS_DIR, file), 'utf-8'));
		if (match) found.push([file, match[1]]);
	}
	return found;
}

describe('media tool icons', () => {
	it('reads every media tool op declaration', () => {
		expect(declaredIcons().length).toBeGreaterThanOrEqual(12);
	});

	it('gives every media tool a glyph in the icon library, not the generic fallback', () => {
		for (const [file, icon] of declaredIcons()) expect(hasIcon(icon), `${file}: ${icon}`).toBe(true);
	});

	it('gives the audio tools the glyphs the Video editor rail uses', () => {
		const icons = Object.fromEntries(declaredIcons());
		expect([icons['mute.py'], icons['volume.py'], icons['replace_audio.py']]).toEqual([OP_ICONS.mute, OP_ICONS.volume, OP_ICONS.replace_audio]);
	});

	it('gives each tool its own glyph', () => {
		const art = declaredIcons().map(([, icon]) => JSON.stringify(getIconPath(icon)));
		expect(new Set(art).size).toBe(art.length);
	});
});
