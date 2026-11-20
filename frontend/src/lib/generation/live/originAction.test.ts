import { describe, expect, it } from 'vitest';
import { resolveOriginAction } from './originAction';

const tab = (over: Record<string, unknown>) => ({ id: 't1', kind: 'generation', generation: { queue: [] }, ...over }) as never;

describe('resolveOriginAction', () => {
	it('offers Open in a tab for MCP only', () => {
		expect(resolveOriginAction({ id: 'g', origin: { kind: 'mcp' } }, [])).toEqual({ kind: 'open-in-tab' });
	});

	it('offers Go to its tab for a tab-started generation only while that tab is open', () => {
		const open = tab({ activeGenerationId: 'g' });
		expect(resolveOriginAction({ id: 'g', origin: null }, [open])).toEqual({ kind: 'go-to-tab', tabId: 't1' });
		expect(resolveOriginAction({ id: 'g', origin: null }, [])).toBeNull();
		expect(resolveOriginAction({ id: 'g', origin: null }, [tab({ activeGenerationId: 'other' })])).toBeNull();
	});

	it('offers Open the edit for an editor origin that names an edit', () => {
		const action = resolveOriginAction({ id: 'g', origin: { kind: 'editor', edit_id: 'ed_1' } }, []);
		expect(action).toMatchObject({ kind: 'open-edit', origin: { editId: 'ed_1' } });
		expect(resolveOriginAction({ id: 'g', origin: { kind: 'editor' } }, [])).toBeNull();
	});

	it('offers nothing for chat', () => {
		expect(resolveOriginAction({ id: 'g', origin: { kind: 'chat' } }, [tab({ activeGenerationId: 'g' })])).toBeNull();
	});
});
