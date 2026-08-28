// @vitest-environment jsdom
//
// The dock reworked its default posture from "everything visible" to a
// compact summary row that expands on demand, and replaced the raw-args
// fallback's "object"/"N items" dead end with a real key -> value tree. These
// mount the real ApprovalDock and check what a reviewer actually sees in
// each state: compact, expanded per `kind`, the untouched legacy shapes, the
// fallback tree, queue position, and mid-resolution "Working".
import { describe, it, expect, vi, afterEach } from 'vitest';
import { tick } from 'svelte';
import type { ToolApprovalPreview, ToolExecution, UnifiedChatMessageData } from '../../src/lib/types/chat';

vi.mock('$lib/services/api/index', () => ({
	api: {
		approveToolExecution: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: ApprovalDock } = await import('../../src/lib/components/chat/ApprovalDock.svelte');
const { createClassComponent } = await import('svelte/legacy');

function execution(overrides: Partial<ToolExecution> = {}): ToolExecution {
	return {
		tool_name: 'start_generation',
		arguments: {},
		result: { success: true, data: '' },
		duration_ms: 0,
		pending_approval: true,
		...overrides
	};
}

function message(overrides: Partial<UnifiedChatMessageData> = {}): UnifiedChatMessageData {
	return {
		id: 'm1',
		role: 'assistant',
		content: '',
		timestamp: 1732000000000,
		...overrides
	};
}

function mount(messages: UnifiedChatMessageData[]) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onResolved = vi.fn();
	const component = createClassComponent({
		component: ApprovalDock as never,
		target,
		props: { messages, sessionId: 's1', onResolved }
	});

	return {
		target,
		component,
		onResolved,
		text: () => target.textContent || '',
		buttons: () => Array.from(target.querySelectorAll('button')),
		byText: (t: string) =>
			Array.from(target.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === t) as
				| HTMLButtonElement
				| undefined,
		dots: (cls: string) => target.querySelectorAll(cls).length,
		async expand() {
			(this.byText('Review full details') as HTMLButtonElement | undefined)?.click();
			await tick();
		}
	};
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.mocked(api.approveToolExecution).mockReset();
});

const generationPreview: ToolApprovalPreview = {
	action: 'Start Generation',
	items: [],
	kind: 'generation',
	summary: 'Krea-2 Photoreal XL · 1216×832',
	fields: [
		{ label: 'Resolution', value: '1216 × 832' },
		{ label: 'Steps', value: '28', old: '20' },
		{ label: 'Sampler', value: 'res_2m', old: 'euler' }
	],
	text_blocks: [{ label: 'Prompt', text: 'cinematic photograph of a lighthouse keeper at dawn' }]
};

describe('compact row (default state)', () => {
	it('shows the summary, key chips with a changed-field dot, and a review affordance — not the full grid', () => {
		const dock = mount([message({ tool_executions: [execution({ preview: generationPreview })] })]);

		expect(dock.text()).toContain('Krea-2 Photoreal XL · 1216×832');
		expect(dock.text()).toContain('steps 28');
		expect(dock.text()).toContain('sampler res_2m');
		expect(dock.byText('Review full details')).toBeTruthy();

		// Changed fields (steps, sampler) each carry a signal dot; resolution didn't change.
		expect(dock.dots('span.bg-signal.rounded-full')).toBe(2);

		// The full settings grid and prompt block aren't in the DOM yet.
		expect(dock.text()).not.toContain('cinematic photograph of a lighthouse keeper');
		expect(dock.byText('Show summary')).toBeUndefined();
	});

	it('derives a compact line when the preview has no explicit summary', () => {
		const preview: ToolApprovalPreview = { action: 'Update prompt', target: 'Lighting', items: [] };
		const dock = mount([message({ tool_executions: [execution({ preview })] })]);
		expect(dock.text()).toContain('Update prompt — Lighting');
	});
});

describe('expanded detail per kind', () => {
	it('kind: generation renders the settings grid (old → new for changed fields) and the prompt block', async () => {
		const dock = mount([message({ tool_executions: [execution({ preview: generationPreview })] })]);
		await dock.expand();

		expect(dock.text()).toContain('cinematic photograph of a lighthouse keeper at dawn');
		expect(dock.text()).toMatch(/20\s*→\s*28/);
		expect(dock.text()).toMatch(/euler\s*→\s*res_2m/);
		expect(dock.byText('Show summary')).toBeTruthy();
	});

	it('kind: timeline renders scene rows with a range chip, text, and an op badge only where present', async () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Prompt Timeline',
			items: [],
			kind: 'timeline',
			rows: [
				{ range: '0:00–0:04.5', text: 'Wide establishing shot', op: 'update' },
				{ range: '0:04.5–0:09.0', text: 'Close-up on the lantern' }
			]
		};
		const dock = mount([message({ tool_executions: [execution({ preview })] })]);
		await dock.expand();

		expect(dock.text()).toContain('0:00–0:04.5');
		expect(dock.text()).toContain('Wide establishing shot');
		expect(dock.text()).toContain('Close-up on the lantern');
		expect(dock.text()).toContain('update');
	});

	it('kind: text_edit renders a stacked old -> new diff, not an inline strikethrough row', async () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Library Prompt',
			items: [],
			kind: 'text_edit',
			text_blocks: [
				{
					label: 'Prompt',
					text: 'lighthouse keeper at golden hour, warm rim light',
					old_text: 'lighthouse keeper, moody blue tones'
				}
			]
		};
		const dock = mount([message({ tool_executions: [execution({ preview })] })]);
		await dock.expand();

		expect(dock.text()).toContain('lighthouse keeper, moody blue tones');
		expect(dock.text()).toContain('changed to');
		expect(dock.text()).toContain('lighthouse keeper at golden hour, warm rim light');
	});
});

describe('legacy previews keep rendering unchanged', () => {
	it('action/target/items/note preview still renders as a chip list with a note', async () => {
		const preview: ToolApprovalPreview = {
			action: 'Create category',
			target: 'Lighting',
			items: ['warm', 'cool', 'neutral'],
			note: 'Applies to future prompts only'
		};
		const dock = mount([message({ tool_executions: [execution({ preview })] })]);
		await dock.expand();

		expect(dock.text()).toContain('Create category');
		expect(dock.text()).toContain('Lighting');
		expect(dock.text()).toContain('warm');
		expect(dock.text()).toContain('Applies to future prompts only');
	});

	it('a `changes` preview still renders through buildDirectorChangeGroups, badge and all', async () => {
		const preview: ToolApprovalPreview = {
			action: 'Update Video Director',
			items: [],
			changes: [
				{
					op: 'update_segment_prompt',
					summary: 'Update prompt on segment seg-1',
					before: { prompt: 'A quiet forest at dawn' },
					after: { prompt: 'A quiet forest at dawn, mist rising' }
				}
			]
		};
		const dock = mount([message({ tool_executions: [execution({ preview })] })]);
		await dock.expand();

		expect(dock.text()).toContain('update');
		expect(dock.text()).toContain('Update prompt on segment seg-1');
		expect(dock.text()).toContain('A quiet forest at dawn, mist rising');
	});

	it('a `proposed_changes` diff still renders old -> new rows with a reason', async () => {
		const data = JSON.stringify({
			proposed_changes: [{ field_name: 'steps', old_value: 20, new_value: 30, reason: 'sharper detail' }]
		});
		const dock = mount([message({ tool_executions: [execution({ result: { success: true, data } })] })]);
		await dock.expand();

		expect(dock.text()).toContain('steps');
		expect(dock.text()).toContain('20');
		expect(dock.text()).toContain('30');
		expect(dock.text()).toContain('sharper detail');
	});
});

describe('generic fallback tree', () => {
	it('never renders the literal words "object" or a bare item count, and labels itself as a fallback', async () => {
		const dock = mount([
			message({
				tool_executions: [
					execution({
						tool_name: 'update_segment_template',
						arguments: {
							template_id: 'svi-chain-v2',
							form_overrides: { steps: 28, sampler: 'res_2m' },
							notify: true
						}
					})
				]
			})
		]);
		expect(dock.text()).toContain('no typed preview — showing raw arguments');

		await dock.expand();

		expect(dock.text()).toContain('"svi-chain-v2"');
		expect(dock.text()).toContain('form_overrides');
		expect(dock.text()).toContain('true');
		// The pre-opened root disclosure shows its children directly.
		expect(dock.text()).toContain('res_2m');
		expect(dock.text()).not.toMatch(/:\s*object\b/);
		expect(dock.text()).not.toContain('2 items');
	});
});

describe('queue', () => {
	it('shows pips and "N of M", and offers Approve all only with more than one pending', () => {
		const dock = mount([
			message({
				tool_executions: [
					execution({ tool_name: 'a' }),
					execution({ tool_name: 'b' }),
					execution({ tool_name: 'c' })
				]
			})
		]);
		expect(dock.text()).toContain('1 of 3');
		expect(dock.dots('.rounded-full.bg-fg, .rounded-full.bg-line-strong')).toBeGreaterThanOrEqual(3);
		expect(dock.byText('Approve all')).toBeTruthy();
		expect(dock.text()).toContain('next: B');
	});

	it('hides Approve all and the pip row for a single pending approval', () => {
		const dock = mount([message({ tool_executions: [execution()] })]);
		expect(dock.text()).not.toContain('1 of 1');
		expect(dock.byText('Approve all')).toBeUndefined();
	});
});

describe('working state', () => {
	it('dims the body and swaps the action row for a spinner while a resolution is in flight', async () => {
		let release: (value: unknown) => void = () => {};
		vi.mocked(api.approveToolExecution).mockImplementation(
			() => new Promise((resolve) => { release = resolve; })
		);

		const dock = mount([message({ tool_executions: [execution()] })]);
		dock.byText('Approve')?.click();
		await tick();

		expect(dock.text()).toContain('Working…');
		expect(dock.byText('Approve')).toBeUndefined();
		expect(dock.byText('Reject')).toBeUndefined();

		release({ success: true, data: { result: { success: true, data: '{}' }, assistant_message: null } });
		await new Promise((r) => setTimeout(r, 0));
		await tick();
		await new Promise((r) => setTimeout(r, 0));
		await tick();

		expect(dock.text()).not.toContain('Working…');
		expect(dock.onResolved).toHaveBeenCalledWith(expect.objectContaining({ messageId: 'm1', approved: true }));
	});
});
