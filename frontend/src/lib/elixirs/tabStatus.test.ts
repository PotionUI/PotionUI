import { describe, expect, it } from 'vitest';
import { elixirTabStatus, isBrewingStatus, stepCounts } from './tabStatus';

const steps = [{ view: { status: 'done' } }, { view: { status: 'done' } }, { view: { status: 'running' } }, { view: null }];

describe('elixir tab status', () => {
	it('shows nothing before the first brew', () => {
		expect(elixirTabStatus(null, [])).toBeNull();
	});

	it('counts done steps of all steps while brewing', () => {
		expect(stepCounts(steps)).toEqual([2, 4]);
		expect(elixirTabStatus({ status: 'running' }, steps)).toEqual({ tone: 'signal', label: 'Brewing, 2 of 4 steps done', count: [2, 4], pulse: true });
		expect(elixirTabStatus({ status: 'pending' }, [])).toEqual({ tone: 'signal', label: 'Brewing', count: null, pulse: true });
	});

	it('rings while it waits for a choice', () => {
		expect(elixirTabStatus({ status: 'awaiting_selection' }, steps)).toMatchObject({ tone: 'signal', ring: true, label: 'Waiting for your choice' });
	});

	it('marks failure in danger', () => {
		expect(elixirTabStatus({ status: 'failed' }, steps)).toEqual({ tone: 'danger', label: 'Brew failed' });
		expect(elixirTabStatus({ status: 'needs_attention' }, steps)).toEqual({ tone: 'danger', label: 'Needs attention' });
	});

	it('shows done only for a background tab that was brewing', () => {
		expect(elixirTabStatus({ status: 'completed' }, steps)).toBeNull();
		expect(elixirTabStatus({ status: 'completed' }, steps, { background: true })).toBeNull();
		expect(elixirTabStatus({ status: 'completed' }, steps, { background: true, wasBrewing: true })).toEqual({ tone: 'success', label: 'Brew done' });
		expect(elixirTabStatus({ status: 'cancelled' }, steps, { background: true, wasBrewing: true })).toBeNull();
	});

	it('knows a brewing or waiting status', () => {
		expect(isBrewingStatus({ tone: 'signal', label: 'x' })).toBe(true);
		expect(isBrewingStatus({ tone: 'success', label: 'x' })).toBe(false);
		expect(isBrewingStatus(null)).toBe(false);
	});
});
