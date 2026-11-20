import { describe, expect, it, vi } from 'vitest';
import { writable } from 'svelte/store';
import { render } from 'svelte/server';
import GenerateOptions from './GenerateOptions.svelte';
import ReplaceBackgroundOptions from './ReplaceBackgroundOptions.svelte';
import { GENERATE_CONTEXT, createGenerateController } from '$lib/tool-tabs/image/generate/controller';
import { createRunEngine } from '$lib/tool-tabs/image/generate/runEngine';

function controller() {
	const ports = { tasks: async () => [], sessions: async () => [], formulas: async () => [], schema: async () => null, setDefault: async () => undefined, clearDefault: async () => undefined, saveSession: vi.fn(), updateSession: vi.fn() };
	const engine = createRunEngine({ start: vi.fn(), get: vi.fn(), cancel: vi.fn(), socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined }) }, { views: writable({}) });
	return createGenerateController({
		getStack: () => ({ steps: [], cursor: 0, pending: null, seq: 0 }),
		setStack: () => undefined,
		source: () => ({ ref: null }),
		editLink: async () => null,
		landRegion: async () => null,
		areaSize: () => null,
		stepsBeneath: async () => [],
		ports: ports as never,
		engine
	});
}

function body(component: unknown, generate: ReturnType<typeof controller>): string {
	return render(component as never, { props: { host: {}, phone: false } as never, context: new Map([[GENERATE_CONTEXT, generate]]) }).body;
}

describe('the Replace the background panel', () => {
	it('offers Find the subject and the four choices, with Describe chosen first', () => {
		const out = body(ReplaceBackgroundOptions, controller());
		expect(out).toContain('data-find-subject');
		expect(out).toContain('Find the subject');
		for (const label of ['Describe', 'Picture', 'Colour', 'Transparent']) expect(out).toContain(label);
		expect(out).toContain('data-background-describe');
		expect(out).not.toContain('data-replace-background-apply');
	});

	it('shows a disabled apply with its reason for a model-free choice before there is a mark', () => {
		const generate = controller();
		generate.setBackground({ mode: 'color' });
		const out = body(ReplaceBackgroundOptions, generate);
		expect(out).toContain('data-replace-background-apply');
		expect(out).toMatch(/data-replace-background-apply[^>]*disabled|disabled[^>]*data-replace-background-apply/);
		expect(out).toContain('Find the subject or mark the background first');
		expect(out).toContain('data-background-color');
	});

	it('asks for a picture before Picture can apply', () => {
		const generate = controller();
		generate.setBackground({ mode: 'picture' });
		const out = body(ReplaceBackgroundOptions, generate);
		expect(out).toContain('No picture chosen');
		expect(out).toContain('data-background-choose');
	});

	it('explains Transparent in a plain note and shows no search error before one ran', () => {
		const generate = controller();
		generate.setBackground({ mode: 'transparent' });
		const out = body(ReplaceBackgroundOptions, generate);
		expect(out).toContain('data-background-transparent');
		expect(out).not.toContain('data-subject-error');
	});
});

describe('the hints of a task', () => {
	it('shows no checkbox for a task without hints', () => {
		const generate = controller();
		generate.use('edit_region');
		expect(body(GenerateOptions, generate)).not.toContain('data-task-hint');
	});

	it('shows Include its shadow, off, on the Remove tool and on it once switched on', () => {
		const generate = controller();
		generate.use('remove_object');
		let out = body(GenerateOptions, generate);
		expect(out).toContain('Include its shadow');
		expect(out).toContain('data-task-hint="include_shadow"');
		expect(out).not.toMatch(/checked/);
		generate.setOption('remove_object', 'include_shadow', true);
		out = body(GenerateOptions, generate);
		expect(out).toMatch(/checked/);
	});
});
