import { describe, it, expect } from 'vitest';
import { applyVariableChanges, type PromptVariableOperation } from './applyVariableChanges';
import type { VariablesMap } from '$lib/utils/variableDefs';

describe('applyVariableChanges', () => {
	it('returns the same map reference when there are no operations', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' } };
		expect(applyVariableChanges(variables, [])).toBe(variables);
		expect(applyVariableChanges(variables, undefined)).toBe(variables);
	});

	it('creates a new text variable', () => {
		const result = applyVariableChanges({}, [{ op: 'set', name: 'mood', type: 'text', value: 'noir' }]);
		expect(result).toEqual({ mood: { type: 'text', value: 'noir' } });
	});

	it('creates a new choice variable with shuffle mode by default', () => {
		const result = applyVariableChanges({}, [
			{ op: 'set', name: 'time', type: 'choice', options: ['dawn', 'dusk'] }
		]);
		expect(result).toEqual({
			time: { type: 'choice', options: ['dawn', 'dusk'], mode: 'shuffle', pinnedIndex: null }
		});
	});

	it('creates a pinned choice variable and keeps pinnedIndex only for pin mode', () => {
		const result = applyVariableChanges({}, [
			{
				op: 'set',
				name: 'time',
				type: 'choice',
				options: ['dawn', 'dusk'],
				mode: 'pin',
				pinned_index: 1
			}
		]);
		expect(result.time).toEqual({
			type: 'choice',
			options: ['dawn', 'dusk'],
			mode: 'pin',
			pinnedIndex: 1
		});
	});

	it('drops pinnedIndex when mode is not pin even if pinned_index was sent', () => {
		const result = applyVariableChanges({}, [
			{
				op: 'set',
				name: 'time',
				type: 'choice',
				options: ['dawn', 'dusk'],
				mode: 'per-image',
				pinned_index: 1
			}
		]);
		expect((result.time as any).pinnedIndex).toBeNull();
	});

	it('replaces an existing variable of a different type', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' } };
		const result = applyVariableChanges(variables, [
			{ op: 'set', name: 'mood', type: 'choice', options: ['noir', 'sunlit'] }
		]);
		expect(result.mood).toEqual({ type: 'choice', options: ['noir', 'sunlit'], mode: 'shuffle', pinnedIndex: null });
	});

	it('removes an existing variable', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' }, time: { type: 'text', value: 'dawn' } };
		const result = applyVariableChanges(variables, [{ op: 'remove', name: 'mood' }]);
		expect(result).toEqual({ time: { type: 'text', value: 'dawn' } });
	});

	it('removing an unknown name is a no-op', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' } };
		const result = applyVariableChanges(variables, [{ op: 'remove', name: 'ghost' }]);
		expect(result).toEqual(variables);
	});

	it('does not mutate the input map', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' } };
		applyVariableChanges(variables, [{ op: 'set', name: 'mood', type: 'text', value: 'sunlit' }]);
		expect(variables.mood).toEqual({ type: 'text', value: 'noir' });
	});

	it('applies multiple operations in order, including set-then-remove on the same name', () => {
		const result = applyVariableChanges({}, [
			{ op: 'set', name: 'mood', type: 'text', value: 'noir' },
			{ op: 'set', name: 'time', type: 'text', value: 'dawn' },
			{ op: 'remove', name: 'mood' }
		]);
		expect(result).toEqual({ time: { type: 'text', value: 'dawn' } });
	});

	it('ignores an operation with no name', () => {
		const result = applyVariableChanges({}, [
			{ op: 'set', name: '', type: 'text', value: 'noir' } as PromptVariableOperation
		]);
		expect(result).toEqual({});
	});

	it('ignores an operation with an unrecognized op', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'noir' } };
		const result = applyVariableChanges(variables, [
			{ op: 'rename', name: 'mood' } as unknown as PromptVariableOperation
		]);
		expect(result).toEqual(variables);
	});

	it('defaults a text set with no value to an empty string', () => {
		const result = applyVariableChanges({}, [{ op: 'set', name: 'mood', type: 'text' }]);
		expect(result.mood).toEqual({ type: 'text', value: '' });
	});
});
