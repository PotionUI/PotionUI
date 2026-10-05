import { describe, it, expect, vi, beforeEach } from 'vitest';

const goto = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('$app/navigation', () => ({ goto }));

import { setPendingRule, startRuleFrom, takePendingRule } from './handoff';
import { parseOrganizeError, problemMessage } from './errors';

beforeEach(() => {
	goto.mockClear();
	takePendingRule();
});

describe('handoff to the editor', () => {
	it('keeps the prefilled rule until the editor takes it, once', async () => {
		await startRuleFrom({ subject: 'generation', conditions: [{ fact: 'model', operator: 'is', value: 'm1' }] });
		expect(goto).toHaveBeenCalledWith('/auto-organize?subject=generations&rule=new');
		expect(takePendingRule()).toMatchObject({ subject: 'generation', conditions: [{ fact: 'model', operator: 'is', value: 'm1' }] });
		expect(takePendingRule()).toBeNull();
	});

	it('goes to the library subject for uploads', async () => {
		await startRuleFrom({ subject: 'upload', conditions: [] });
		expect(goto).toHaveBeenCalledWith('/auto-organize?subject=uploads&rule=new');
	});

	it('survives a reload through session storage', () => {
		setPendingRule({ subject: 'model', conditions: [] });
		expect(takePendingRule()?.subject).toBe('model');
	});
});

describe('error parsing', () => {
	it('reads the plain message, code and problems from the API error body', () => {
		const err = Object.assign(new Error('x'), {
			isAxiosError: true,
			response: { data: { detail: { error: 'invalid_rule', message: 'Not valid', problems: [{ path: 'a', code: 'bad_value', message: 'Pick a value.' }] } } }
		});
		const info = parseOrganizeError(err);
		expect(info.code).toBe('invalid_rule');
		expect(problemMessage(info)).toBe('Pick a value.');
	});

	it('carries the running job id on a conflict', () => {
		const err = Object.assign(new Error('x'), {
			isAxiosError: true,
			response: { data: { detail: { error: 'job_running', message: 'Already running', job_id: 'j1' } } }
		});
		expect(parseOrganizeError(err).jobId).toBe('j1');
	});

	it('falls back for unknown errors', () => {
		expect(parseOrganizeError(new Error('boom'), 'Nope').message).toBe('Nope');
	});
});
