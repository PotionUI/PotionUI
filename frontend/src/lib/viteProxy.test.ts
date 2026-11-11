import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CONFIG = readFileSync(join(__dirname, '..', '..', 'vite.config.ts'), 'utf-8');
const ENTRY_RE = /'(\/(?:api|health|ws))': \{([\s\S]*?)\n\t+\}/g;

describe('vite proxy', () => {
	it('forwards the client address on every proxy entry, so the backend can tell a remote browser from a local one', () => {
		const entries = [...CONFIG.matchAll(ENTRY_RE)];
		expect(entries.length).toBe(6);
		const missing = entries
			.filter((entry) => !/\bxfwd: true\b/.test(entry[2]))
			.map((entry) => entry[1]);
		expect(missing).toEqual([]);
	});
});
