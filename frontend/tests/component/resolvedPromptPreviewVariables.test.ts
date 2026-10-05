// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { tick } from 'svelte';
import type { VariablesMap, VariableOption } from '$lib/utils/variableDefs';

const { default: ResolvedPromptPreview } = await import(
	'../../src/lib/components/ResolvedPromptPreview.svelte'
);
const { resolvePreviewVariables, substituteVariables } = await import('../../src/lib/utils/resolvedVariables');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ResolvedPromptPreview as never, target, props });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

const text = (value: string) => ({ type: 'text' as const, value });
const choice = (options: VariableOption[], mode: 'shuffle' | 'pin' | 'per-image', pinnedIndex: number | null = null) => ({
	type: 'choice' as const,
	options,
	mode,
	pinnedIndex
});

describe('Resolved prompt row and variables', () => {
	it('substitutes a text variable in the collapsed row', () => {
		const variables: VariablesMap = { potion_color: text('electric blue') };
		mounted = mount({ prompt: 'glowing ${potion_color} liquid', variables });
		const row = mounted.target.textContent || '';
		expect(row).toContain('glowing electric blue liquid');
		expect(row).not.toContain('${potion_color}');
	});

	it('substitutes a pinned choice with its pinned option', () => {
		const variables: VariablesMap = { mood: choice(['calm', 'wild'], 'pin', 1) };
		mounted = mount({ prompt: 'a ${mood} sea', variables });
		expect(mounted.target.textContent).toContain('a wild sea');
	});

	it('shows a shuffle variable as its option group and says it shuffles', async () => {
		const variables: VariablesMap = { mood: choice(['calm', 'wild'], 'shuffle') };
		mounted = mount({ prompt: 'a ${mood} sea', variables });
		expect(mounted.target.textContent).toContain('a {calm|wild} sea');
		(mounted.target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		expect(mounted.target.textContent).toContain('mood');
		expect(mounted.target.textContent).toContain('shuffles each generation');
	});

	it('shows a per-image variable as its group and says it re-rolls per image', async () => {
		const variables: VariablesMap = { mood: choice(['calm', 'wild'], 'per-image') };
		mounted = mount({ prompt: 'a ${mood} sea', variables });
		(mounted.target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		expect(mounted.target.textContent).toContain('re-rolls per image');
	});

	it('does not note variables the prompt does not use', async () => {
		const variables: VariablesMap = { mood: choice(['calm', 'wild'], 'shuffle') };
		mounted = mount({ prompt: 'plain text', variables });
		(mounted.target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		expect(mounted.target.textContent).not.toContain('shuffles each generation');
	});

	it('substitutes in the negative prompt too', async () => {
		const variables: VariablesMap = { bad: text('blurry') };
		mounted = mount({ prompt: 'a cat', negativePrompt: 'no ${bad}', variables });
		(mounted.target.querySelector('button') as HTMLButtonElement).click();
		await tick();
		Array.from(mounted.target.querySelectorAll('button'))
			.find((b) => b.textContent?.trim() === 'Negative')
			?.click();
		await tick();
		expect(mounted.target.textContent).toContain('no blurry');
	});

	it('leaves an undefined variable as written', () => {
		mounted = mount({ prompt: 'a ${ghost} sea', variables: {} });
		expect(mounted.target.textContent).toContain('a ${ghost} sea');
	});
});

describe('resolvePreviewVariables', () => {
	it('narrows a conditional option by a pinned dependency, as submit does', () => {
		const variables: VariablesMap = {
			music: choice(['jazz', 'techno'], 'pin', 0),
			dance: choice(
				[
					{ text: 'swing', when: { var: 'music', values: ['jazz'] } },
					{ text: 'rave', when: { var: 'music', values: ['techno'] } },
					'waltz'
				],
				'shuffle'
			)
		};
		const { replacements } = resolvePreviewVariables(variables);
		expect(replacements.music).toBe('jazz');
		expect(replacements.dance).toBe('{swing|waltz}');
	});

	it('collapses to the single eligible option without a note', () => {
		const variables: VariablesMap = {
			music: text('techno'),
			dance: choice([{ text: 'swing', when: { var: 'music', values: ['jazz'] } }, { text: 'rave', when: { var: 'music', values: ['techno'] } }], 'shuffle')
		};
		const { replacements, notes } = resolvePreviewVariables(variables);
		expect(replacements.dance).toBe('rave');
		expect(notes).toEqual([]);
	});

	it('substituteVariables only touches known names', () => {
		expect(substituteVariables('${a} ${b}', { a: 'x' })).toBe('x ${b}');
	});
});
