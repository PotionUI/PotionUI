// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

registerFieldComponent('string', { component: TextInput });
registerFieldComponent('row', { component: RowField });

function flipsWith(other: string) {
	return [
		{ when: { field: other, equals: 'one' }, then: { set_value: 'two' } },
		{ when: { field: other, equals: 'two' }, then: { set_value: 'one' } }
	];
}

const PING_PONG = {
	properties: {
		pair: {
			type: 'row',
			children: [
				{ name: 'left', type: 'string', title: 'Left', default: 'one', reactions: flipsWith('right') },
				{ name: 'right', type: 'string', title: 'Right', default: 'one', reactions: flipsWith('left') },
				{ name: 'steady', type: 'string', title: 'Steady', default: 'fixed' }
			]
		}
	}
};

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let destroy: (() => void) | undefined;

beforeEach(() => {
	clearSchemaCache();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: PING_PONG }
	} as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	vi.restoreAllMocks();
});

describe('DynamicForm reaction non-convergence', () => {
	it('stops after a bounded number of rounds and warns once naming the fields that kept changing', async () => {
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		const target = document.createElement('div');
		document.body.appendChild(target);
		const log: Array<Record<string, unknown>> = [];
		const form = createClassComponent({
			component: DynamicForm as never,
			target,
			props: {
				presetId: `native-${Math.random()}`,
				mode: 'pingpong',
				onFormDataChange: (data: Record<string, unknown>) => log.push(data)
			}
		}) as unknown as { $destroy: () => void };
		destroy = () => {
			form.$destroy();
			target.remove();
		};

		await settle();

		expect(log.length).toBeGreaterThan(0);
		expect(log.length).toBeLessThan(50);
		const messages = warn.mock.calls.map((call) => call.map(String).join(' '));
		const reactionWarnings = messages.filter((message) => message.includes('left') && message.includes('right'));
		expect(reactionWarnings).toHaveLength(1);
		expect(reactionWarnings[0]).not.toContain('steady');
	});
});
