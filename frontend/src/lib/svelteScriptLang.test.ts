import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC_ROOT = join(__dirname, '..');
const SCRIPT_TAG_RE = /<script\b[^>]*>/g;
const TS_LANG_RE = /\blang=["']ts["']/;

function walkSvelteFiles(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		if (statSync(full).isDirectory()) {
			out.push(...walkSvelteFiles(full));
		} else if (entry.endsWith('.svelte')) {
			out.push(full);
		}
	}
	return out;
}

describe('svelte script blocks', () => {
	it('every script block in a component declares the same lang, so the Vite dependency scan can parse it', () => {
		const mixed: string[] = [];
		for (const file of walkSvelteFiles(SRC_ROOT)) {
			const tags = readFileSync(file, 'utf8').match(SCRIPT_TAG_RE) ?? [];
			const langs = new Set(tags.map((tag) => TS_LANG_RE.test(tag)));
			if (langs.size > 1) mixed.push(`${relative(SRC_ROOT, file)}: ${tags.join(' ')}`);
		}
		expect(mixed).toEqual([]);
	});
});
