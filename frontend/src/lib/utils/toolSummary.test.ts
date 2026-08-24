import { describe, it, expect } from 'vitest';
import { getToolSummary, type ToolExecutionLike } from './toolSummary';

function exec(data: unknown, success = true, error?: string): ToolExecutionLike {
	return {
		tool_name: 'any_tool',
		arguments: {},
		result: { success, data: typeof data === 'string' ? data : JSON.stringify(data), error }
	};
}

describe('getToolSummary', () => {
	it('returns the error message on failure', () => {
		expect(getToolSummary(exec({}, false, 'boom'))).toBe('boom');
	});

	it('returns Failed when failure has no error message', () => {
		expect(getToolSummary(exec({}, false))).toBe('Failed');
	});

	it('summarizes model lists with pluralization', () => {
		expect(getToolSummary(exec({ models: [{}, {}] }))).toBe('2 models');
		expect(getToolSummary(exec({ models: [{}] }))).toBe('1 model');
	});

	it('summarizes counts', () => {
		expect(getToolSummary(exec({ count: 0 }))).toBe('0 results');
		expect(getToolSummary(exec({ count: 1 }))).toBe('1 result');
	});

	it('summarizes categories with irregular plural', () => {
		expect(getToolSummary(exec({ categories: [{}] }))).toBe('1 category');
		expect(getToolSummary(exec({ categories: [{}, {}] }))).toBe('2 categories');
	});

	it('summarizes prompts, values, templates, segments, fields', () => {
		expect(getToolSummary(exec({ prompts: [{}, {}, {}] }))).toBe('3 prompts');
		expect(getToolSummary(exec({ values: [1, 2] }))).toBe('2 values');
		expect(getToolSummary(exec({ templates: [1] }))).toBe('1 template');
		expect(getToolSummary(exec({ segments: [] }))).toBe('0 segments');
		expect(getToolSummary(exec({ fields: { a: 1, b: 2 } }))).toBe('2 fields');
	});

	it('summarizes generation start ids', () => {
		expect(getToolSummary(exec({ generation_id: 'abcdef1234567890' }))).toBe(
			'Started: abcdef12...'
		);
	});

	it('falls back to action, then message, then OK', () => {
		expect(getToolSummary(exec({ action: 'apply_form_changes' }))).toBe('apply_form_changes');
		expect(getToolSummary(exec({ message: 'done it' }))).toBe('done it');
		expect(getToolSummary(exec({ other: true }))).toBe('OK');
	});

	it('returns OK for unparseable data', () => {
		expect(getToolSummary(exec('not json'))).toBe('OK');
	});
});
