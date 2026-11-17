import { describe, it, expect } from 'vitest';
import type { GenerationQueueSnapshot } from '$lib/types/api';
import type { GenerationTab } from '$lib/types/tabs';
import { ELSEWHERE_LIMIT, findTabFollowing, formatElapsed, selectRunningElsewhere } from './select';

function tab(id: string, extra: Partial<GenerationTab> = {}): GenerationTab {
	return {
		kind: 'generation',
		id,
		name: id,
		selectedPreset: null,
		selectedMode: null,
		selectedSessionId: null,
		activeGenerationId: null,
		generation: { queue: [] },
		...extra
	} as unknown as GenerationTab;
}

const MCP = { kind: 'mcp', key_id: 'k1', key_name: 'claude desktop', key_current_name: 'claude desktop' };

function running(id: string, extra: Record<string, unknown> = {}) {
	return {
		generation_id: id,
		tab_id: null,
		status: 'running',
		has_tab: false,
		preset_name: 'SDXL',
		mode: 'txt2img',
		started_at: '2026-11-17T09:00:00+00:00',
		created_at: '2026-11-17T08:59:00+00:00',
		origin: MCP,
		...extra
	};
}

function pending(id: string, position: number, extra: Record<string, unknown> = {}) {
	return {
		generation_id: id,
		tab_id: null,
		queue_position: position,
		status: 'pending',
		has_tab: false,
		preset_name: 'Flux',
		mode: 'txt2img',
		created_at: '2026-11-17T09:01:00+00:00',
		started_at: null,
		origin: MCP,
		...extra
	};
}

function snapshot(runningItems: unknown[], pendingItems: unknown[] = []): GenerationQueueSnapshot {
	return { running: runningItems, pending: pendingItems } as unknown as GenerationQueueSnapshot;
}

describe('selectRunningElsewhere', () => {
	it('lists a generation the server says no tab owns', () => {
		const result = selectRunningElsewhere(snapshot([running('g1')]), [tab('t1')]);
		expect(result.total).toBe(1);
		expect(result.items[0]).toMatchObject({
			generationId: 'g1',
			status: 'running',
			presetName: 'SDXL',
			mode: 'txt2img',
			origin: { kind: 'mcp', label: 'Started over MCP · claude desktop' }
		});
	});

	it('lists a generation whose tab id matches no tab open in this browser', () => {
		const result = selectRunningElsewhere(snapshot([running('g1', { tab_id: 'other-window', has_tab: true })]), [tab('t1')]);
		expect(result.items.map((i) => i.generationId)).toEqual(['g1']);
	});

	it('leaves out a generation started from a tab open here', () => {
		const result = selectRunningElsewhere(snapshot([running('g1', { tab_id: 't1', has_tab: true })]), [tab('t1')]);
		expect(result).toEqual({ items: [], total: 0 });
	});

	it('leaves out a generation a tab here already follows, however it is tracked', () => {
		const tabs = [
			tab('t1', { activeGenerationId: 'g1' }),
			tab('t2', { generation: { queue: [{ generation_id: 'g2', queue_position: null, status: 'running' }] } } as Partial<GenerationTab>),
			tab('t3', { openedFrom: { generationId: 'g3', kind: 'mcp', label: 'x' } })
		];
		const result = selectRunningElsewhere(snapshot([running('g1'), running('g2'), running('g3'), running('g4')]), tabs);
		expect(result.items.map((i) => i.generationId)).toEqual(['g4']);
	});

	it('puts running ones first, oldest first, then pending ones in queue order', () => {
		const result = selectRunningElsewhere(
			snapshot(
				[running('late', { started_at: '2026-11-17T09:05:00+00:00' }), running('early', { started_at: '2026-11-17T09:00:00+00:00' })],
				[pending('p2', 1), pending('p1', 0)]
			),
			[tab('t1')]
		);
		expect(result.items.map((i) => i.generationId)).toEqual(['early', 'late', 'p1', 'p2']);
		expect(result.items[2]).toMatchObject({ status: 'pending', queuePosition: 0 });
	});

	it('is bounded, but still counts everything', () => {
		const many = Array.from({ length: ELSEWHERE_LIMIT + 5 }, (_, index) => running(`g${index}`));
		const result = selectRunningElsewhere(snapshot(many), [tab('t1')]);
		expect(result.items).toHaveLength(ELSEWHERE_LIMIT);
		expect(result.total).toBe(ELSEWHERE_LIMIT + 5);
	});

	it('lists only what was started over MCP, never editor runs, chat runs or untagged ones', () => {
		const result = selectRunningElsewhere(
			snapshot([
				running('mcp'),
				running('editor', { origin: { kind: 'editor', edit_id: 'e1', step_id: null, task: 'edit_region', run_id: 'r1' }, mode: 'editor:edit_region' }),
				running('chat', { origin: { kind: 'chat' } }),
				running('none', { origin: null }),
				running('unknown', { origin: { kind: 'something-new' } })
			]),
			[tab('t1')]
		);
		expect(result.items.map((i) => i.generationId)).toEqual(['mcp']);
		expect(result.total).toBe(1);
	});

	it('shows nothing before the first snapshot', () => {
		expect(selectRunningElsewhere(null, [tab('t1')])).toEqual({ items: [], total: 0 });
	});
});

describe('findTabFollowing', () => {
	it('finds the tab opened for a generation, or tracking it, and nothing else', () => {
		const tabs = [tab('t1', { openedFrom: { generationId: 'g1', kind: null, label: null } }), tab('t2', { activeGenerationId: 'g2' })];
		expect(findTabFollowing(tabs, 'g1')).toBe('t1');
		expect(findTabFollowing(tabs, 'g2')).toBe('t2');
		expect(findTabFollowing(tabs, 'g3')).toBeNull();
	});
});

describe('formatElapsed', () => {
	it('counts seconds, minutes and hours since the start', () => {
		const start = 1_000_000;
		expect(formatElapsed(start, start + 9_000)).toBe('9s');
		expect(formatElapsed(start, start + 125_000)).toBe('2m 05s');
		expect(formatElapsed(start, start + 3_900_000)).toBe('1h 05m');
		expect(formatElapsed(null, start)).toBe('');
	});
});
