import { describe, expect, it } from 'vitest';
import { applyRunUpdate, initialAutomationRunsState } from './automationRuns';
import type { AutomationRunUpdateMessage } from '$lib/types/automations';

describe('applyRunUpdate', () => {
	it('applies a run-level update (no node_id) to activeRunStatus/activeRunError', () => {
		const state = initialAutomationRunsState();
		const message: AutomationRunUpdateMessage = {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			status: 'running'
		};

		const next = applyRunUpdate(state, message);
		expect(next.activeRunId).toBe('run-1');
		expect(next.activeRunStatus).toBe('running');
		expect(next.activeRunError).toBeNull();
		expect(next.nodeStatuses).toEqual({});
	});

	it('applies a node-level update (node_id present) to nodeStatuses, leaving run status untouched', () => {
		const state = { ...initialAutomationRunsState(), activeRunId: 'run-1', activeRunStatus: 'running' as const };
		const message: AutomationRunUpdateMessage = {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'success'
		};

		const next = applyRunUpdate(state, message);
		expect(next.nodeStatuses).toEqual({ 'node-a': 'success' });
		expect(next.activeRunStatus).toBe('running');
	});

	it('accumulates multiple node-level updates for the same run', () => {
		let state = initialAutomationRunsState();
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'running'
		});
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'success'
		});
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-b',
			status: 'waiting'
		});

		expect(state.nodeStatuses).toEqual({ 'node-a': 'success', 'node-b': 'waiting' });
	});

	it('resets nodeStatuses when a node-level update arrives for a different run', () => {
		let state = initialAutomationRunsState();
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'success'
		});
		expect(state.nodeStatuses).toEqual({ 'node-a': 'success' });

		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-2',
			automation_id: 'auto-1',
			node_id: 'node-x',
			status: 'running'
		});

		expect(state.activeRunId).toBe('run-2');
		expect(state.nodeStatuses).toEqual({ 'node-x': 'running' });
	});

	it('resets nodeStatuses when a run-level update starts tracking a new run', () => {
		let state = initialAutomationRunsState();
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'success'
		});
		state = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-2',
			automation_id: 'auto-1',
			status: 'running'
		});

		expect(state.nodeStatuses).toEqual({});
		expect(state.activeRunStatus).toBe('running');
	});

	it('patches the matching row in `runs` on a run-level update, leaving others untouched', () => {
		const state = {
			...initialAutomationRunsState(),
			runs: [
				{
					id: 'run-1',
					automation_id: 'auto-1',
					status: 'running' as const,
					started_at: '2026-01-01T00:00:00Z'
				},
				{
					id: 'run-0',
					automation_id: 'auto-1',
					status: 'success' as const,
					started_at: '2025-12-31T00:00:00Z'
				}
			]
		};

		const next = applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			status: 'failed',
			error: 'boom'
		});

		expect(next.runs.find((r) => r.id === 'run-1')?.status).toBe('failed');
		expect(next.runs.find((r) => r.id === 'run-1')?.error).toBe('boom');
		expect(next.runs.find((r) => r.id === 'run-0')?.status).toBe('success');
	});

	it('is a pure function: does not mutate the input state', () => {
		const state = initialAutomationRunsState();
		const frozen = JSON.parse(JSON.stringify(state));
		applyRunUpdate(state, {
			type: 'automation_run_update',
			run_id: 'run-1',
			automation_id: 'auto-1',
			node_id: 'node-a',
			status: 'running'
		});
		expect(state).toEqual(frozen);
	});
});
