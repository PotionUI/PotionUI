import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { prepareRequestFromSession } from './prepareRequest';

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '../../../../tests/fixtures/session_requests');
const CAPTURE = process.env.CAPTURE_SESSION_FIXTURES === '1';

interface Fixture {
	preset_vars: Record<string, unknown>;
	mode: string;
	entry: Record<string, unknown>;
	overrides: Record<string, unknown>;
	random: number[];
	can_batch: boolean;
	tab_id: string;
	refusal?: string;
	expected?: Record<string, unknown>;
	expected_refusal?: string;
}

function sequence(values: number[]) {
	let index = 0;
	return () => values[index++ % values.length];
}

const files = readdirSync(FIXTURES)
	.filter((name) => name.endsWith('.json'))
	.sort();

describe('session request parity fixtures', () => {
	it('has fixtures', () => {
		expect(files.length).toBeGreaterThan(0);
	});

	for (const file of files) {
		const path = join(FIXTURES, file);
		const fixture = JSON.parse(readFileSync(path, 'utf8')) as Fixture;
		it(file, () => {
			if (fixture.refusal) {
				expect(fixture.expected).toBeUndefined();
				return;
			}
			const result = prepareRequestFromSession({
				presetId: 'preset-fixture',
				mode: fixture.mode,
				session: fixture.entry,
				overrides: fixture.overrides,
				presetVars: fixture.preset_vars,
				tabId: fixture.tab_id,
				canBatch: fixture.can_batch,
				random: sequence(fixture.random),
				now: () => 0
			});
			if (!result.ok) {
				if (CAPTURE) {
					const { expected: _drop, ...rest } = fixture;
					writeFileSync(path, JSON.stringify({ ...rest, expected_refusal: result.code }, null, 2) + '\n');
					return;
				}
				expect(fixture.expected_refusal).toBe(result.code);
				return;
			}
			const request = JSON.parse(JSON.stringify(result.request));
			if (CAPTURE) {
				const { expected_refusal: _drop, ...rest } = fixture;
				writeFileSync(path, JSON.stringify({ ...rest, expected: request }, null, 2) + '\n');
				return;
			}
			expect(fixture.expected_refusal).toBeUndefined();
			expect(request).toEqual(fixture.expected);
		});
	}
});
