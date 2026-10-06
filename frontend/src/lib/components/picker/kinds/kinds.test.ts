import { describe, expect, it } from 'vitest';
import type { PresetInfo } from '$lib/types/api';
import type { LLMConfig } from '$lib/types/llm';
import type { Plan, LimitKindDescriptor } from '$lib/plans/types';
import {
	cloudModelsKind,
	createPlansKind,
	groupsKind,
	llmsKind,
	modelsKind,
	presetsKind,
	usersKind
} from './index';
import { presetModesLabel, presetSourceLabel, presetUseLabel } from './presets';
import { planLimitsSummary } from './plans';

const kinds = [presetsKind, llmsKind, usersKind, groupsKind, modelsKind, createPlansKind([]), cloudModelsKind];

describe('kind configs', () => {
	it('every kind declares at least one base fact and a default sort that exists', () => {
		for (const kind of kinds as any[]) {
			expect(kind.facts.some((f: any) => f.base)).toBe(true);
			expect(kind.sorts.map((s: any) => s.value)).toContain(kind.defaultSort);
			expect(kind.columns.length).toBeGreaterThan(0);
			expect(new Set(kind.columns.map((c: any) => c.key)).size).toBe(kind.columns.length);
		}
	});

	it('presets columns and filters', () => {
		expect(presetsKind.columns.map((c) => c.key)).toEqual(['category', 'modes', 'use', 'needs']);
		expect(presetsKind.filters.map((f) => f.key)).toEqual(['engine', 'source', 'category', 'mode', 'same_name']);
		expect(presetsKind.facts.filter((f) => f.base).map((f) => f.key)).toEqual(['engine', 'source']);
	});

	it('llms columns', () => {
		expect(llmsKind.columns.map((c) => c.key)).toEqual(['context', 'max_out', 'vision']);
		const config = {
			id: '1',
			name: 'Llama',
			type: 'ollama',
			enabled: true,
			base_url: 'http://localhost:11434/v1',
			model: 'llama3',
			max_tokens: 4096,
			supports_vision: true,
			provider_options: { num_ctx: 32768 }
		} as unknown as LLMConfig;
		const values = Object.fromEntries(llmsKind.columns.map((c) => [c.key, c.value(config)]));
		expect(values).toEqual({ context: '32.8k', max_out: '4.1k', vision: 'yes' });
		expect(llmsKind.facts.find((f) => f.key === 'endpoint')?.value(config)).toBe('localhost:11434');
	});

	it('users, groups, models, plans and cloud columns', () => {
		expect(usersKind.columns.map((c) => c.key)).toEqual(['groups', 'plan', 'last_login']);
		expect(groupsKind.columns.map((c) => c.key)).toEqual(['members', 'presets', 'llms', 'models']);
		expect(modelsKind.columns.map((c) => c.key)).toEqual(['type', 'size', 'copies']);
		expect(createPlansKind([]).columns.map((c) => c.key)).toEqual(['assigned']);
		expect(cloudModelsKind.columns.map((c) => c.key)).toEqual(['tasks', 'price', 'enabled']);
	});
});

describe('preset labels', () => {
	const base: PresetInfo = { id: 'p', name: 'P', version: '1', tags: [] };

	it('derives the source from origin and falls back to the legacy source', () => {
		expect(presetSourceLabel({ ...base, origin: { kind: 'plugin', plugin_id: 'comfyui-backend', path: 'x' } })).toBe(
			'plugin: comfyui-backend'
		);
		expect(presetSourceLabel({ ...base, origin: { kind: 'local', plugin_id: null, path: 'x' } })).toBe('local');
		expect(presetSourceLabel({ ...base, source: 'custom' })).toBe('local');
		expect(presetSourceLabel({ ...base, source: 'official' })).toBe('marketplace');
	});

	it('summarises modes and usage', () => {
		expect(presetModesLabel({ ...base, modes: ['txt2img', 'img2img', 'edit'] })).toBe('txt2img, img2img +1');
		expect(presetModesLabel({ ...base, modes: ['txt2img'] })).toBe('txt2img');
		expect(presetUseLabel({ ...base, assignment_count: 1, group_count: 2 })).toBe('1 user · 2 grp');
	});
});

describe('plans kind', () => {
	it('summarises the limits of a plan', () => {
		const descriptor = {
			key: 'generations_per_day',
			label: 'Generations per day',
			value_type: 'count',
			unit: 'generations',
			input_scale: 1,
			window: 'day'
		} as unknown as LimitKindDescriptor;
		const plan = { id: 'p', name: 'Free', description: '', is_system: false, limits: [{ kind: 'generations_per_day', value: 20 }] } as Plan;
		expect(planLimitsSummary(plan, [descriptor])).toContain('20');
		expect(planLimitsSummary({ ...plan, limits: [] }, [descriptor])).toBe('No limits');
	});
});
