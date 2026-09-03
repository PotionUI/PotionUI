import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import {
	provideContext,
	collectProvidedContext,
	declareMode,
	declaredMode,
	onToolApplied,
	dispatchToolApplied,
	resetPageContext
} from './pageContext';

describe('pageContext', () => {
	beforeEach(() => {
		resetPageContext();
	});

	describe('provideContext / collectProvidedContext', () => {
		it('merges every registered provider under its own key', () => {
			provideContext('wizard', () => ({ step: 2 }));
			provideContext('form', () => ({ preset: 'sdxl' }));

			expect(collectProvidedContext()).toEqual({
				wizard: { step: 2 },
				form: { preset: 'sdxl' }
			});
		});

		it('skips a provider that returns null', () => {
			provideContext('wizard', () => null);
			provideContext('form', () => ({ preset: 'sdxl' }));

			expect(collectProvidedContext()).toEqual({ form: { preset: 'sdxl' } });
		});

		it('unregister stops the provider from being collected', () => {
			const unregister = provideContext('wizard', () => ({ step: 2 }));
			unregister();

			expect(collectProvidedContext()).toEqual({});
		});

		it('a later registration under the same key replaces the earlier one', () => {
			provideContext('wizard', () => ({ step: 1 }));
			provideContext('wizard', () => ({ step: 2 }));

			expect(collectProvidedContext()).toEqual({ wizard: { step: 2 } });
		});
	});

	describe('declareMode', () => {
		it('is null when nothing has declared a mode', () => {
			expect(get(declaredMode)).toBeNull();
		});

		it('becomes the declared mode while the declaration is active', () => {
			const unregister = declareMode('comfyui-import-wizard');
			expect(get(declaredMode)).toBe('comfyui-import-wizard');
			unregister();
			expect(get(declaredMode)).toBeNull();
		});

		it('the most recently declared mode wins over an earlier still-active one', () => {
			const unregisterFirst = declareMode('mode-a');
			const unregisterSecond = declareMode('mode-b');
			expect(get(declaredMode)).toBe('mode-b');

			unregisterSecond();
			// Restores to the still-active earlier declaration, not to null.
			expect(get(declaredMode)).toBe('mode-a');

			unregisterFirst();
			expect(get(declaredMode)).toBeNull();
		});
	});

	describe('onToolApplied / dispatchToolApplied', () => {
		it('runs every handler registered for the tool name and reports it was handled', () => {
			const seen: Record<string, unknown>[] = [];
			onToolApplied('import_comfyui_workflow', (result) => seen.push(result));

			const handled = dispatchToolApplied('import_comfyui_workflow', { workflow: 'x' });

			expect(handled).toBe(true);
			expect(seen).toEqual([{ workflow: 'x' }]);
		});

		it('returns false, and calls no handler, for a tool with no registration', () => {
			const handled = dispatchToolApplied('some_other_tool', { foo: 'bar' });
			expect(handled).toBe(false);
		});

		it('unregister stops the handler from running and, once it is the last one, reports unhandled', () => {
			const seen: Record<string, unknown>[] = [];
			const unregister = onToolApplied('import_comfyui_workflow', (result) => seen.push(result));
			unregister();

			const handled = dispatchToolApplied('import_comfyui_workflow', { workflow: 'x' });

			expect(handled).toBe(false);
			expect(seen).toEqual([]);
		});
	});

	describe('resetPageContext', () => {
		it('clears providers, mode declarations, and tool handlers', () => {
			provideContext('wizard', () => ({ step: 2 }));
			declareMode('comfyui-import-wizard');
			onToolApplied('import_comfyui_workflow', () => {});

			resetPageContext();

			expect(collectProvidedContext()).toEqual({});
			expect(get(declaredMode)).toBeNull();
			expect(dispatchToolApplied('import_comfyui_workflow', {})).toBe(false);
		});
	});
});
