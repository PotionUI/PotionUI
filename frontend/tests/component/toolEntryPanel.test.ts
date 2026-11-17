// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { writable } from 'svelte/store';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';
import PluginToolOptions from './toolTabs/PluginToolOptions.svelte';

vi.mock('$lib/services/api/index', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	api: new Proxy({}, { get: () => () => undefined })
}));

const { default: ToolEntryPanel } = await import('$lib/components/tool-tabs/frame/ToolEntryPanel.svelte');
const { resetFailures, hasFailed } = await import('$lib/editors/guard');

const SCHEMA = [{ type: 'slider', name: 'amount', title: 'Amount', minimum: 0, maximum: 100, default: 50, step: 1 }];

function fakeHost() {
	const pending = writable<unknown>(null);
	const calls = { begin: [] as any[], update: [] as any[] };
	const host = {
		pending: { subscribe: pending.subscribe },
		begin: (draft: any) => {
			calls.begin.push(draft);
			pending.set({ ...draft, target: null });
		},
		update: (draft: any) => {
			calls.update.push(draft);
			pending.set({ ...draft, target: null });
		}
	};
	return { host, calls, pending };
}

const range = () => document.body.querySelector<HTMLInputElement>('input[type="range"]')!;
const drag = (value: number) => {
	range().value = String(value);
	range().dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
};

afterEach(() => {
	document.body.innerHTML = '';
	resetFailures();
});

describe('ToolEntryPanel', () => {
	it('draws a declared schema and, without handlers, drives a pending step of the tool itself', async () => {
		const { host, calls } = fakeHost();
		mountComponent(ToolEntryPanel as never, { entry: { id: 'acme.form', editor: 'image', group: 'g', label: 'Form', icon: 'extension', schema: SCHEMA }, entryKey: 'image:acme.form', host });
		await settle(10);
		expect(range().value).toBe('50');
		drag(70);
		expect(calls.begin).toHaveLength(1);
		expect(calls.begin[0]).toMatchObject({ tool: 'acme.form', label: 'Form', summary: 'Amount 70', params: { amount: 70 } });
		drag(80);
		range().dispatchEvent(new Event('change', { bubbles: true }));
		await settle(10);
		expect(calls.begin).toHaveLength(1);
		expect(calls.update).toHaveLength(1);
		expect(calls.update[0].params).toEqual({ amount: 80 });
	});

	it('hands values, commit and the context to the tool, and shows the values it returns', async () => {
		const { host } = fakeHost();
		const seen: Array<{ values: any; info: any; tool: string }> = [];
		const entry = {
			id: 'acme.own',
			editor: 'image',
			group: 'g',
			label: 'Own',
			icon: 'extension',
			schema: (ctx: { tool: string }) => (ctx.tool === 'acme.own' ? SCHEMA : []),
			values: () => ({ amount: 12 }),
			onValues: (values: unknown, info: unknown, ctx: { tool: string }) => seen.push({ values, info, tool: ctx.tool })
		};
		mountComponent(ToolEntryPanel as never, { entry, entryKey: 'image:acme.own', host });
		await settle(10);
		expect(range().value).toBe('12');
		drag(33);
		range().dispatchEvent(new Event('change', { bubbles: true }));
		await settle(10);
		expect(seen[0]).toMatchObject({ values: { amount: 33 }, info: { commit: false, name: 'amount' }, tool: 'acme.own' });
		expect(seen.filter((call) => call.info.commit)).toHaveLength(1);
	});

	it('draws the schema first and the options component below it', async () => {
		const { host } = fakeHost();
		const entry = { id: 'acme.both', editor: 'image', group: 'g', label: 'Both', icon: 'extension', schema: SCHEMA, options: { component: PluginToolOptions }, description: 'Does both' };
		mountComponent(ToolEntryPanel as never, { entry, entryKey: 'image:acme.both', host });
		await settle(10);
		const panel = document.body.querySelector('[data-entry-options="acme.both"]')!;
		expect(panel.querySelector('[data-effect-description]')?.textContent).toBe('Does both');
		const form = panel.querySelector('[data-entry-form]')!;
		const options = panel.querySelector('[data-plugin-options]');
		expect(form.compareDocumentPosition(options!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('reports a schema that throws as a failed entry and draws nothing', async () => {
		const { host } = fakeHost();
		const entry = { id: 'acme.bad', editor: 'image', group: 'g', label: 'Bad', icon: 'extension', schema: () => { throw new Error('boom'); } };
		mountComponent(ToolEntryPanel as never, { entry, entryKey: 'image:acme.bad', host });
		await settle(10);
		expect(document.body.querySelector('[data-entry-form]')).toBeNull();
		expect(hasFailed('image:acme.bad')).toBe(true);
	});
});
