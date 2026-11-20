import { describe, expect, it } from 'vitest';
import fixtures from './fixtures/video-payloads.json';
import { runReference } from './payloadFlow';
import { SCENARIOS } from './payloadScenarios';

function plain(value: unknown): unknown {
	const copy = JSON.parse(JSON.stringify(value));
	for (const row of copy.rows) delete row.icon;
	return copy;
}

describe('video payload fixtures captured before the editor was realigned', () => {
	for (const scenario of SCENARIOS) {
		it(`${scenario.name} compiles to the captured request`, () => {
			expect(plain(runReference(scenario))).toEqual(plain((fixtures as Record<string, unknown>)[scenario.name]));
		});
	}

	it('covers every scenario', () => {
		expect(Object.keys(fixtures).sort()).toEqual(SCENARIOS.map((scenario) => scenario.name).sort());
	});
});
