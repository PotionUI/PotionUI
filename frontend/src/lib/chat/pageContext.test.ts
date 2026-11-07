import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	provideContext,
	collectProvidedContext,
	declareMode,
	declaredMode,
	onToolApplied,
	dispatchToolApplied,
	dispatchToolResult,
	dispatchExecutedToolResults,
	toolResultId,
	resetPageContext,
	type ExecutedTool,
	type ToolAppliedHandler
} from './pageContext';

function executed(tool_name: string, data: unknown, extra: Partial<ExecutedTool> = {}): ExecutedTool {
	return {
		tool_name,
		result: { success: true, data: typeof data === 'string' ? data : JSON.stringify(data) },
		...extra
	};
}

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
			onToolApplied('import_comfyui_workflow', (result) => {
				seen.push(result);
			});

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
			const unregister = onToolApplied('import_comfyui_workflow', (result) => {
				seen.push(result);
			});
			unregister();

			const handled = dispatchToolApplied('import_comfyui_workflow', { workflow: 'x' });

			expect(handled).toBe(false);
			expect(seen).toEqual([]);
		});

		it('returns a handler-reported ToolAppliedOutcome instead of the bare `true` "handled" flag', () => {
			onToolApplied('propose_form_changes', () => ({ status: 'stale', message: 'the draft moved on' }));

			const result = dispatchToolApplied('propose_form_changes', {});

			expect(result).toEqual({ status: 'stale', message: 'the draft moved on' });
		});

		it('falls back to `true` when a handler returns something that is not a ToolAppliedOutcome', () => {
			// Plugins are plain compiled JS, not type-checked against
			// ToolAppliedHandler at runtime - a handler can return a value
			// that isn't `void` or a real outcome (here: a plain number) even
			// though this test's own TS types wouldn't let it. The cast
			// simulates that; the guard in dispatchToolApplied is what must
			// not mistake it for a status-bearing outcome.
			const returnsANumber = ((result: Record<string, unknown>) => [result].length) as unknown as ToolAppliedHandler;
			onToolApplied('import_comfyui_workflow', returnsANumber);

			const handled = dispatchToolApplied('import_comfyui_workflow', { workflow: 'x' });

			expect(handled).toBe(true);
		});
	});

	describe('dispatchToolResult', () => {
		it('parses the JSON data and runs the handlers once per result id', () => {
			const seen: Record<string, unknown>[] = [];
			onToolApplied('save_shot', (result) => {
				seen.push(result);
			});

			const first = dispatchToolResult(toolResultId('m1', 0), 'save_shot', '{"shot":3}');
			const again = dispatchToolResult(toolResultId('m1', 0), 'save_shot', '{"shot":3}');

			expect(first).toEqual({ data: { shot: 3 }, outcome: true });
			expect(again).toBeNull();
			expect(seen).toEqual([{ shot: 3 }]);
		});

		it('hands back the parsed data when no plugin handles the tool, so core can apply it', () => {
			expect(dispatchToolResult('m1:0', 'propose_form_changes', '{"action":"apply_form_changes"}')).toEqual({
				data: { action: 'apply_form_changes' },
				outcome: false
			});
		});

		it('skips a result whose data is not JSON', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);

			expect(dispatchToolResult('m1:0', 'save_shot', 'Saved shot 3.')).toBeNull();
			expect(dispatchToolResult('m1:1', 'save_shot', undefined)).toBeNull();
			expect(handler).not.toHaveBeenCalled();
		});
	});

	describe('dispatchExecutedToolResults', () => {
		it('runs the handlers of every tool that executed without approval', () => {
			const shots: Record<string, unknown>[] = [];
			const scenes: Record<string, unknown>[] = [];
			onToolApplied('save_shot', (result) => {
				shots.push(result);
			});
			onToolApplied('save_scene', (result) => {
				scenes.push(result);
			});

			const outcome = dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [executed('save_shot', { shot: 1 }), executed('save_scene', { scene: 2 }, { approval_status: null })]
			});

			expect(outcome).toBeNull();
			expect(shots).toEqual([{ shot: 1 }]);
			expect(scenes).toEqual([{ scene: 2 }]);
		});

		it('leaves out tools that wait for approval, were rejected, failed, expired, were skipped, still run or returned text', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);

			dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [
					executed('save_shot', { status: 'pending_approval', shot: 1 }, { approval_status: 'pending' }),
					executed('save_shot', { shot: 2 }, { approval_status: 'rejected' }),
					executed('save_shot', { shot: 3 }, { approval_status: 'failed' }),
					executed('save_shot', { status: 'pending_approval', shot: 7 }, { approval_status: 'expired' }),
					executed('save_shot', { shot: 4 }, { skipped: true }),
					executed('save_shot', { shot: 5 }, { result: { success: false, data: '{"shot":5}' } }),
					executed('save_shot', { shot: 6 }, { status: 'running' }),
					executed('save_shot', 'Saved.')
				]
			});

			expect(handler).not.toHaveBeenCalled();
		});

		it('delivers the confirmed result of a tool approved after the message was finalized', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);
			const message = {
				id: 'm1',
				tool_executions: [executed('save_shot', { status: 'pending_approval', shot: 1 }, { approval_status: 'pending' })]
			};

			dispatchExecutedToolResults(message);
			expect(handler).not.toHaveBeenCalled();

			const dispatched = dispatchToolResult(toolResultId('m1', 0), 'save_shot', '{"shot":1,"version":4}');
			expect(dispatched?.outcome).toBe(true);
			expect(handler).toHaveBeenCalledTimes(1);
			expect(handler).toHaveBeenCalledWith({ shot: 1, version: 4 });

			dispatchExecutedToolResults({ id: 'm1', tool_executions: [executed('save_shot', { shot: 1, version: 4 }, { approval_status: 'approved' })] });
			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('delivers a tool the host approved before the message was finalized', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);

			dispatchExecutedToolResults({ id: 'm1', tool_executions: [executed('save_shot', { shot: 1 }, { approval_status: 'approved' })] });

			expect(handler).toHaveBeenCalledWith({ shot: 1 });
		});

		it('runs a handler once when the same message is delivered live and then recovered', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);
			const message = { id: 'm1', tool_executions: [executed('save_shot', { shot: 1 })] };

			dispatchExecutedToolResults(message);
			dispatchExecutedToolResults({ ...message, tool_executions: [...message.tool_executions] });

			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('never runs an approved tool a second time', () => {
			const handler = vi.fn();
			onToolApplied('apply_storyboard', handler);
			dispatchToolResult(toolResultId('m1', 1), 'apply_storyboard', '{"shots":2}');

			dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [executed('list_shots', { shots: [] }), executed('apply_storyboard', { shots: 2 })]
			});

			expect(handler).toHaveBeenCalledTimes(1);
		});

		it('tells apart two executions of the same tool in one message', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);

			dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [executed('save_shot', { shot: 1 }), executed('save_shot', { shot: 2 })]
			});

			expect(handler.mock.calls.map(([result]) => result)).toEqual([{ shot: 1 }, { shot: 2 }]);
		});

		it('does nothing for a message without an id', () => {
			const handler = vi.fn();
			onToolApplied('save_shot', handler);

			expect(dispatchExecutedToolResults({ tool_executions: [executed('save_shot', { shot: 1 })] })).toBeNull();
			expect(handler).not.toHaveBeenCalled();
		});

		it('reports the last stale or partial outcome and ignores applied and noop ones', () => {
			onToolApplied('save_shot', () => ({ status: 'stale', message: 'The shot list changed.' }));
			onToolApplied('save_scene', () => ({ status: 'applied' }));
			onToolApplied('save_take', () => ({ status: 'noop' }));

			const outcome = dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [executed('save_shot', {}), executed('save_scene', {}), executed('save_take', {})]
			});

			expect(outcome).toEqual({ status: 'stale', message: 'The shot list changed.' });
		});

		it('keeps going after a handler throws', () => {
			const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
			const handler = vi.fn();
			onToolApplied('save_shot', () => {
				throw new Error('plugin bug');
			});
			onToolApplied('save_scene', handler);

			dispatchExecutedToolResults({
				id: 'm1',
				tool_executions: [executed('save_shot', {}), executed('save_scene', { scene: 1 })]
			});

			expect(handler).toHaveBeenCalledWith({ scene: 1 });
			expect(errors).toHaveBeenCalled();
			errors.mockRestore();
		});
	});

	describe('resetPageContext', () => {
		it('forgets which tool results were already dispatched', () => {
			dispatchToolResult('m1:0', 'save_shot', '{}');

			resetPageContext();

			expect(dispatchToolResult('m1:0', 'save_shot', '{}')).not.toBeNull();
		});

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
