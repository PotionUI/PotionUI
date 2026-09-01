import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

/**
 * Guard for the Detail family standard (docs: detail-panel-standard). Mirrors
 * tests/architecture/test_layering.py's spirit for the frontend: walk
 * routes/**\/*.svelte and fail, naming file+line, on the literals a hand-rolled
 * master-detail right pane would reintroduce instead of using DetailSection /
 * DetailHeader.
 *
 * ALLOWLIST is every current violator, seeded once when this guard landed.
 * Shrinking it is the migration's progress meter — a file drops out only when
 * it's migrated onto the detail/ family, never to silence a new violation.
 */

const ROUTES_ROOT = join(__dirname, '..', '..', '..', 'routes');

const SHADOW_RAISED_ALLOWLIST = new Set<string>([]);

const TRACKING_ALLOWLIST = new Set([
	'admin/components/GenerationsTab.svelte',
	'admin/components/SessionsDebugTab.svelte',
	'admin/components/PresetsTab.svelte'
]);

const TITLE_SIZE_ALLOWLIST = new Set([
	'admin/components/PresetsTab.svelte',
	'docs/components/live/LayoutDataReference.svelte'
]);

const SHADOW_RAISED_RE = /shadow-raised/;
const TRACKING_RE = /tracking-\[0\.07em\]/;
const TITLE_SIZE_RE = /text-lg font-semibold|text-2xl font-semibold/;
const MASTER_DETAIL_IMPORT_RE = /MasterDetailLayout/;
const DETAIL_SLOT_RE = /slot="detail"/;

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

function findViolations(text: string, pattern: RegExp): number[] {
	const lines: number[] = [];
	const re = new RegExp(pattern.source, 'g');
	let match: RegExpExecArray | null;
	while ((match = re.exec(text)) !== null) {
		lines.push(lineOf(text, match.index));
	}
	return lines;
}

describe('detail family adherence guard', () => {
	const files = walkSvelteFiles(ROUTES_ROOT).map((full) => ({
		full,
		rel: relative(ROUTES_ROOT, full).split('\\').join('/')
	}));

	it('finds routes to scan', () => {
		expect(files.length).toBeGreaterThan(0);
	});

	it('never uses a hand-rolled shadow-raised box in a master-detail file, unless allowlisted', () => {
		const offenders: string[] = [];
		for (const { full, rel } of files) {
			const text = readFileSync(full, 'utf-8');
			if (!MASTER_DETAIL_IMPORT_RE.test(text)) continue;
			if (SHADOW_RAISED_ALLOWLIST.has(rel)) continue;
			for (const line of findViolations(text, SHADOW_RAISED_RE)) {
				offenders.push(`${rel}:${line}`);
			}
		}
		expect(offenders).toEqual([]);
	});

	it('never hand-writes the DetailSection heading literal in a master-detail file, unless allowlisted', () => {
		const offenders: string[] = [];
		for (const { full, rel } of files) {
			const text = readFileSync(full, 'utf-8');
			if (!MASTER_DETAIL_IMPORT_RE.test(text)) continue;
			if (TRACKING_ALLOWLIST.has(rel)) continue;
			for (const line of findViolations(text, TRACKING_RE)) {
				offenders.push(`${rel}:${line}`);
			}
		}
		expect(offenders).toEqual([]);
	});

	it('never uses an outlawed title size inside a slot="detail" file, unless allowlisted', () => {
		const offenders: string[] = [];
		for (const { full, rel } of files) {
			const text = readFileSync(full, 'utf-8');
			if (!DETAIL_SLOT_RE.test(text)) continue;
			if (TITLE_SIZE_ALLOWLIST.has(rel)) continue;
			for (const line of findViolations(text, TITLE_SIZE_RE)) {
				offenders.push(`${rel}:${line}`);
			}
		}
		expect(offenders).toEqual([]);
	});

	it('does not allowlist files that no longer violate (allowlist entries must exist under routes/)', () => {
		const known = new Set(files.map((f) => f.rel));
		for (const rel of [...SHADOW_RAISED_ALLOWLIST, ...TRACKING_ALLOWLIST, ...TITLE_SIZE_ALLOWLIST]) {
			expect(known.has(rel)).toBe(true);
		}
	});
});
