import { describe, expect, it } from 'vitest';
import fixtures from './fixtures/video-payloads.json';
import { runReference } from './payloadFlow';
import { SCENARIOS } from './payloadScenarios';

describe('video payload fixtures captured before the editor was realigned', () => {
	for (const scenario of SCENARIOS) {
		it(`${scenario.name} compiles to the captured request`, () => {
			expect(JSON.parse(JSON.stringify(runReference(scenario)))).toEqual((fixtures as Record<string, unknown>)[scenario.name]);
		});
	}

	it('covers every scenario', () => {
		expect(Object.keys(fixtures).sort()).toEqual(SCENARIOS.map((scenario) => scenario.name).sort());
	});
});
