import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { DeriveError, deriveBlock, evaluateDerive, parseDerive, type DeriveEnv, type DeriveScalar } from './derive';

interface ExpressionCase {
	expr: string;
	env?: DeriveEnv;
	value?: DeriveScalar;
	error?: 'parse' | 'eval';
}

interface BlockCase {
	name: string;
	env: DeriveEnv;
	derive: Record<string, string>;
	values: Record<string, DeriveScalar>;
}

const golden = JSON.parse(
	readFileSync(new URL('../../../../tests/fixtures/effects/derive/cases.json', import.meta.url), 'utf8')
) as { tolerance: number; expressions: ExpressionCase[]; blocks: BlockCase[] };

function same(actual: DeriveScalar, expected: DeriveScalar) {
	if (typeof expected === 'number') {
		expect(typeof actual).toBe('number');
		expect(Math.abs((actual as number) - expected)).toBeLessThanOrEqual(golden.tolerance * Math.max(1, Math.abs(expected)));
	} else {
		expect(actual).toBe(expected);
	}
}

function stageOf(run: () => unknown): string | null {
	try {
		run();
		return null;
	} catch (error) {
		return error instanceof DeriveError ? error.stage : 'other';
	}
}

describe('the derive language matches the shared goldens', () => {
	it('has goldens for values, parse errors and evaluation errors', () => {
		const kinds = new Set(golden.expressions.map((entry) => entry.error ?? 'value'));
		expect([...kinds].sort()).toEqual(['eval', 'parse', 'value']);
	});

	for (const entry of golden.expressions) {
		it(`${JSON.stringify(entry.expr)} → ${entry.error ? `${entry.error} error` : JSON.stringify(entry.value)}`, () => {
			if (entry.error === 'parse') {
				expect(stageOf(() => parseDerive(entry.expr))).toBe('parse');
				return;
			}
			const node = parseDerive(entry.expr);
			if (entry.error === 'eval') {
				expect(stageOf(() => evaluateDerive(node, entry.env ?? {}))).toBe('eval');
				return;
			}
			same(evaluateDerive(node, entry.env ?? {}), entry.value!);
		});
	}

	for (const block of golden.blocks) {
		it(`evaluates the ${block.name} block in order`, () => {
			const values = deriveBlock(block.derive, block.env);
			expect(Object.keys(values)).toEqual(Object.keys(block.values));
			for (const [name, expected] of Object.entries(block.values)) same(values[name], expected);
		});
	}
});
