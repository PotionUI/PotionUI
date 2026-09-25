import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC_ROOT = join(__dirname, '..', '..', '..');

const ALLOWLIST = new Set<string>([
	'routes/admin/components/BackendOptimizations.svelte',
	'routes/prompts/components/PromptWorkspace.svelte'
]);

const BASE_MODAL_RE = /<BaseModal[\s>]/;
const CONFIRM_FOOTER_RE = /ConfirmFooter/;
const BUTTON_ELEMENT_RE = /<(Button|button)\b[^>]*>([\s\S]*?)<\/\1>/g;

function walkSvelteFiles(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const stat = statSync(full);
		if (stat.isDirectory()) {
			out.push(...walkSvelteFiles(full));
		} else if (entry.endsWith('.svelte')) {
			out.push(full);
		}
	}
	return out;
}

function lineOf(text: string, index: number): number {
	return text.slice(0, index).split('\n').length;
}

function bareButtonLabel(inner: string): string {
	return inner
		.replace(/<[^>]*>/g, ' ')
		.replace(/\s+/g, ' ')
		.trim();
}

function findCancelButtonLines(text: string): number[] {
	const lines: number[] = [];
	const re = new RegExp(BUTTON_ELEMENT_RE.source, 'g');
	let match: RegExpExecArray | null;
	while ((match = re.exec(text)) !== null) {
		const label = bareButtonLabel(match[2]);
		if (/^Cancel\b/.test(label)) lines.push(lineOf(text, match.index));
	}
	return lines;
}

describe('modal Cancel/confirm standard guard', () => {
	const files = walkSvelteFiles(SRC_ROOT).map((full) => ({
		full,
		rel: relative(SRC_ROOT, full).split('\\').join('/')
	}));

	it('finds files to scan', () => {
		expect(files.length).toBeGreaterThan(0);
	});

	it('every BaseModal usage with a Cancel button routes its footer through ConfirmFooter, unless allowlisted', () => {
		const offenders: string[] = [];
		for (const { full, rel } of files) {
			if (ALLOWLIST.has(rel)) continue;
			const text = readFileSync(full, 'utf-8');
			if (!BASE_MODAL_RE.test(text)) continue;
			if (CONFIRM_FOOTER_RE.test(text)) continue;
			for (const line of findCancelButtonLines(text)) {
				offenders.push(`${rel}:${line}`);
			}
		}
		expect(offenders).toEqual([]);
	});

	it('does not allowlist files that no longer exist under src/', () => {
		const known = new Set(files.map((f) => f.rel));
		for (const rel of ALLOWLIST) {
			expect(known.has(rel)).toBe(true);
		}
	});
});
