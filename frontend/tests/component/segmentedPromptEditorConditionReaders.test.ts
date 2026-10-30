// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: SegmentedPromptEditor } = await import('../../src/lib/components/SegmentedPromptEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { api } = await import('../../src/lib/services/api/index');
vi.spyOn(api, 'getClient').mockReturnValue({
	get: vi.fn().mockResolvedValue({ data: { data: {} } }),
	put: vi.fn().mockResolvedValue({ data: { success: true } })
} as never);
const { tick } = await import('svelte');

const variables = { age: { type: 'number', mode: 'shuffle', min: 18, max: 80, step: 1, value: 18 } };

function segment(id: string, content: string) {
	return { id, content, type: 'content', chips: {}, enabled: true };
}

async function mount(segments: ReturnType<typeof segment>[]) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: { segments, variables }
	});
	await tick();
	await tick();
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('SegmentedPromptEditor condition readers', () => {
	it('links a shuffle number to one roll when a condition in another segment reads it', async () => {
		const target = await mount([
			segment('a', 'old ${age} and ${age}'),
			segment('b', 'hair <<if $age > 65>>grey<<else>>dark<<end>>')
		]);
		const states = Array.from(target.querySelectorAll('.variable-chip .chip-state')).map((el) => el.textContent);
		expect(states).toEqual(['Linked 2', 'Linked 2']);
	});

	it('leaves usages independent when no condition reads the variable', async () => {
		const target = await mount([segment('a', 'old ${age} and ${age}'), segment('b', 'plain')]);
		const states = Array.from(target.querySelectorAll('.variable-chip .chip-state')).map((el) => el.textContent);
		expect(states).toEqual(['1/2', '2/2']);
	});
});
