import { describe, expect, it, vi } from 'vitest';
import type { EditRecord } from '$lib/services/api/edits';
import { continueEditInNewTab, registerSavedCopyWriter, type ContinueEnv } from './continueEdit';
import { readEditLink } from './image/editLink';
import { emptyImageState, readImageState, stackSignature, unsavedEdits, writeImageState } from './image/state';
import { apply, beginEdit, createStack } from './frame/editStack';
import { resizeDraft } from './image/steps';

function failure(status: number) {
	return { response: { status, data: { detail: {} } } };
}

function stateWithStep(): Record<string, unknown> {
	const stack = apply(beginEdit(createStack(), resizeDraft({ width: 100, height: 80, lock: true })));
	return writeImageState({ ...emptyImageState('resize'), stack });
}

function record(tool = 'image'): EditRecord {
	return {
		id: 'ed_1',
		tool: tool as 'image',
		name: null,
		source: { kind: 'library', id: 'orig-1', name: 'orig.png' },
		thumbnail_upload_id: null,
		revision: 5,
		created_at: null,
		updated_at: null,
		state: stateWithStep()
	};
}

function setup(options: { tabs?: Array<{ id: string; kind: string; tool?: string; state?: unknown }>; getEdit?: ContinueEnv['getEdit']; tool?: string } = {}) {
	const opened: Array<{ tool: string; source: unknown; options: Record<string, unknown> }> = [];
	const focused: string[] = [];
	const env: ContinueEnv = {
		getEdit: options.getEdit ?? vi.fn(async () => ({ success: true, data: { edit: record(options.tool) } })),
		library: {
			getLibraryItem: vi.fn(async (id: string) => ({
				success: true,
				data: { id, filename: 'o.png', original_filename: 'o.png', media_type: 'image', url: `/api/media/uploads/${id}.png`, tags: [] }
			})) as never
		},
		tabs: {
			list: () => options.tabs ?? [],
			focus: (id) => void focused.push(id),
			open: (tool, source, openOptions) => {
				opened.push({ tool, source, options: openOptions as Record<string, unknown> });
				return 'tab-new';
			}
		}
	};
	return { env, opened, focused };
}

describe('continueEditInNewTab', () => {
	it('focuses the tab that already holds the edit and fetches nothing', async () => {
		const getEdit = vi.fn();
		const { env, opened, focused } = setup({ tabs: [{ id: 'tab-7', kind: 'tool', tool: 'image', state: { edit: { id: 'ed_1', revision: 3 } } }], getEdit });
		expect(await continueEditInNewTab('ed_1', 'pic-1', env)).toEqual({ kind: 'focused', tabId: 'tab-7' });
		expect(focused).toEqual(['tab-7']);
		expect(opened).toEqual([]);
		expect(getEdit).not.toHaveBeenCalled();
	});

	it('opens a new tab of the edit tool with the steps, the link and the picture as the saved copy', async () => {
		const { env, opened, focused } = setup();
		expect(await continueEditInNewTab('ed_1', 'pic-1', env)).toEqual({ kind: 'opened', tabId: 'tab-new' });
		expect(opened).toHaveLength(1);
		const [call] = opened;
		expect(call.tool).toBe('image');
		expect(call.source).toMatchObject({ item_id: 'orig-1' });
		expect(call.options.fresh).toBe(true);
		expect(call.options.activeTool).toBe('resize');
		const state = call.options.state as Record<string, unknown>;
		expect(readEditLink(state)).toEqual({ id: 'ed_1', revision: 5 });
		const parsed = readImageState(state);
		expect(parsed.saved?.itemId).toBe('pic-1');
		expect(parsed.saved?.signature).toBe(stackSignature(parsed.stack));
		expect(unsavedEdits(parsed.stack, parsed.saved)).toBe(0);
		expect(focused).toEqual(['tab-new']);
	});

	it('reads the tool from the edit instead of assuming the Image editor', async () => {
		const { env, opened } = setup({ tool: 'acme.sketch' });
		await continueEditInNewTab('ed_1', 'pic-1', env);
		expect(opened[0].tool).toBe('acme.sketch');
		expect((opened[0].options.state as Record<string, unknown>).saved).toBeNull();
		registerSavedCopyWriter('acme.sketch', (state, itemId) => ({ ...state, saved: { itemId } }));
		await continueEditInNewTab('ed_1', 'pic-2', env);
		expect((opened[1].options.state as Record<string, unknown>).saved).toEqual({ itemId: 'pic-2' });
	});

	it('keeps the saved copy of the edit when no picture is given', async () => {
		const { env, opened } = setup();
		await continueEditInNewTab('ed_1', null, env);
		expect(readImageState(opened[0].options.state).saved).toBeNull();
	});

	it('reports a deleted edit and opens nothing', async () => {
		const { env, opened } = setup({ getEdit: vi.fn(async () => Promise.reject(failure(404))) });
		expect(await continueEditInNewTab('ed_1', 'pic-1', env)).toEqual({ kind: 'missing' });
		expect(opened).toEqual([]);
	});

	it('reports other failures and a picture that is gone', async () => {
		const failing = setup({ getEdit: vi.fn(async () => Promise.reject(failure(500))) });
		expect(await continueEditInNewTab('ed_1', 'pic-1', failing.env)).toEqual({ kind: 'failed' });
		const gone = setup();
		(gone.env.library.getLibraryItem as ReturnType<typeof vi.fn>).mockRejectedValueOnce(failure(404));
		expect(await continueEditInNewTab('ed_1', 'pic-1', gone.env)).toEqual({ kind: 'source-missing' });
		expect(gone.opened).toEqual([]);
	});
});
