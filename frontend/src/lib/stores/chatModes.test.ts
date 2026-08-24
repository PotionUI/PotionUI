import { describe, it, expect, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getChatModes: vi.fn(), listChatTools: vi.fn() }
}));

import { resolveModeForRoute, resolveModeName, toolsForMode } from './chatModes';
import type { ChatMode, ChatToolInfo } from '$lib/types/chat';

function mode(id: string, prefixes: string[]): ChatMode {
	return {
		id,
		name: id,
		description: '',
		default_route_prefixes: prefixes,
		tools: [],
		source: 'test'
	};
}

describe('resolveModeForRoute', () => {
	const modes = [
		mode('generation', ['/', '/generate']),
		mode('dataset-generator', ['/plugins/dataset-generator']),
		mode('models-helper', ['/models'])
	];

	it('resolves the generate page to generation', () => {
		expect(resolveModeForRoute('/generate', modes)).toBe('generation');
		expect(resolveModeForRoute('/generate/tab-1', modes)).toBe('generation');
	});

	it('resolves plugin routes by their registered prefixes', () => {
		expect(resolveModeForRoute('/plugins/dataset-generator', modes)).toBe('dataset-generator');
		expect(resolveModeForRoute('/plugins/dataset-generator/editor/5', modes)).toBe(
			'dataset-generator'
		);
	});

	it('longest matching prefix wins over the root prefix', () => {
		expect(resolveModeForRoute('/models', modes)).toBe('models-helper');
		expect(resolveModeForRoute('/models/abc123', modes)).toBe('models-helper');
	});

	it('root prefix catches unrelated routes', () => {
		expect(resolveModeForRoute('/history', modes)).toBe('generation');
	});

	it('is segment-aware: /modelsx does not match /models', () => {
		expect(resolveModeForRoute('/modelsx', modes)).toBe('generation');
	});

	it('ignores trailing slashes on both sides', () => {
		expect(resolveModeForRoute('/generate/', modes)).toBe('generation');
		expect(resolveModeForRoute('/plugins/dataset-generator/', modes)).toBe('dataset-generator');
	});

	it('falls back to generation when nothing matches or modes are empty', () => {
		expect(resolveModeForRoute('/anywhere', [mode('x', ['/y'])])).toBe('generation');
		expect(resolveModeForRoute('/anywhere', [])).toBe('generation');
	});
});

describe('toolsForMode', () => {
	const tools: ChatToolInfo[] = [
		{ name: 'global_tool', description: '', hint: '', mode: null },
		{ name: 'gen_tool', description: '', hint: '', mode: 'generation' },
		{ name: 'plugin_tool', description: '', hint: '', mode: 'dataset-generator' }
	];

	it('returns mode tools plus global tools only', () => {
		expect(toolsForMode(tools, 'generation').map((t) => t.name)).toEqual([
			'global_tool',
			'gen_tool'
		]);
		expect(toolsForMode(tools, 'dataset-generator').map((t) => t.name)).toEqual([
			'global_tool',
			'plugin_tool'
		]);
	});
});

describe('resolveModeName', () => {
	const modes: ChatMode[] = [
		{ ...mode('history', []), name: 'History assistant' },
		{ ...mode('models', []), name: 'Models assistant' }
	];

	it('returns the registered display name for a known mode id', () => {
		expect(resolveModeName('models', modes)).toBe('Models assistant');
	});

	it('falls back to the raw id for an unknown mode', () => {
		expect(resolveModeName('unknown-mode', modes)).toBe('unknown-mode');
	});
});
