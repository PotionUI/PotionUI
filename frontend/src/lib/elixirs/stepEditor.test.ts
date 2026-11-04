import { describe, it, expect, vi } from 'vitest';
import type { GenerationTab } from '$lib/types/tabs';
import { createStepEditor, type CommitResult, type StepEditorDeps } from './stepEditor';
import type { StepOption } from './stepEdit';
import type { ElixirNode, FollowedState } from './types';

const OPTIONS: StepOption[] = [
	{ id: 'a', label: 'Orbit left', detail: 'Wan 2.2 · Image to video', follow: false },
	{ id: 'b', label: 'Close-up reveal', detail: 'Krea 2 · Text to image', follow: true }
];

function makeNode(id: string, over: Partial<ElixirNode> = {}): ElixirNode {
	return {
		id,
		kind: 'preset',
		preset_id: 'wan',
		mode: 'i2v',
		overrides: {},
		session_mode: 'copy',
		session_snapshot: { prompt: 'orbit', formData: { steps: 20 } },
		...over
	};
}

function makeTab(over: Record<string, unknown> = {}) {
	return { id: 'emb', formPublished: true, formData: { steps: 20 }, prompt: 'orbit', ...over } as unknown as GenerationTab;
}

function setup(options: { nodes?: Record<string, ElixirNode>; followed?: Record<string, FollowedState>; commit?: () => Promise<CommitResult>; settle?: boolean } = {}) {
	let revision = 5;
	let nodes: Record<string, ElixirNode> = options.nodes ?? { a: makeNode('a'), b: makeNode('b', { session_mode: 'follow', session_ref: { id: 's1', name: 'Bottle orbit' } }) };
	let tab: (GenerationTab & { embedded?: unknown }) | null = null;
	let listener: ((value: GenerationTab | null) => void) | null = null;
	let sessionDirty: ((dirty: boolean) => void) | null = null;
	let remote: { revision: number; nodes: Record<string, ElixirNode> } | null = null;
	const calls = {
		open: vi.fn(),
		close: vi.fn(),
		setBaseline: vi.fn(),
		setTitle: vi.fn(),
		setDirty: vi.fn(),
		loadSession: vi.fn(async () => {}),
		saveSession: vi.fn(async () => true),
		adopt: vi.fn(),
		saved: vi.fn(),
		error: vi.fn(),
		onReturn: vi.fn(),
		commit: vi.fn()
	};
	let clock = 0;
	const timers: Array<{ run: () => void; ms: number; id: number }> = [];

	const deps: StepEditorDeps = {
		elixirName: () => 'Potion study',
		options: () => OPTIONS,
		node: (id) => nodes[id] ?? null,
		followed: (id) => options.followed?.[id] ?? (id === 'b' ? { status: 'ok', session: { name: 'Bottle orbit' } } : undefined),
		revision: () => revision,
		settle: async () => options.settle !== false,
		fetchLatest: async () => {
			const snapshot = remote ?? { revision, nodes };
			return { revision: snapshot.revision, node: (id) => snapshot.nodes[id] ?? null, record: snapshot };
		},
		adopt: (record) => {
			calls.adopt(record);
			const next = record as { revision: number; nodes: Record<string, ElixirNode> };
			revision = next.revision;
			nodes = next.nodes;
		},
		commit: async (nodeId, overrides) => {
			calls.commit(nodeId, overrides);
			const result = options.commit ? await options.commit() : 'saved';
			if (result === 'saved') {
				nodes = { ...nodes, [nodeId]: { ...nodes[nodeId], overrides } };
				revision += 1;
			}
			return result;
		},
		tabs: {
			open: (data) => {
				calls.open(data);
				tab = { ...makeTab(), ...data, formPublished: true, embedded: { nodeId: data.nodeId, persistence: data.persistence } } as unknown as GenerationTab;
				return 'emb';
			},
			close: () => {
				calls.close();
				tab = null;
			},
			current: () => tab,
			setBaseline: (baseline) => calls.setBaseline(baseline),
			subscribe: (run) => {
				listener = run;
				run(tab);
				return () => {
					listener = null;
				};
			}
		},
		host: { setTitle: calls.setTitle, setDirty: calls.setDirty },
		loadSession: calls.loadSession,
		saveSession: calls.saveSession,
		watchSessionDirty: (_id, run) => {
			sessionDirty = run;
			return () => {
				sessionDirty = null;
			};
		},
		notify: { error: calls.error, saved: calls.saved },
		onReturn: calls.onReturn,
		now: () => clock,
		timers: {
			set: (run, ms) => {
				const id = timers.length + 1;
				timers.push({ run, ms, id });
				return id;
			},
			clear: (handle) => {
				const at = timers.findIndex((item) => item.id === handle);
				if (at >= 0) timers.splice(at, 1);
			}
		}
	};
	const editor = createStepEditor(deps);

	const edit = (over: Record<string, unknown>) => {
		tab = { ...(tab as GenerationTab), ...over } as GenerationTab;
		listener?.(tab);
	};
	const advance = (ms: number) => {
		clock += ms;
	};

	return {
		editor,
		calls,
		edit,
		advance,
		timers,
		setRemote: (next: typeof remote) => (remote = next),
		nodes: () => nodes,
		sessionDirty: (dirty: boolean) => sessionDirty?.(dirty),
		revision: () => revision
	};
}

describe('opening a step', () => {
	it('opens a Copy step locally with its settings and the elixir title', async () => {
		const t = setup();
		expect(await t.editor.open('a')).toBe(true);
		expect(t.calls.open).toHaveBeenCalledWith(
			expect.objectContaining({ nodeId: 'a', persistence: 'local', selectedPreset: 'wan', selectedMode: 'i2v', prompt: 'orbit', name: 'Orbit left' })
		);
		expect(t.calls.setTitle).toHaveBeenCalledWith('Potion study › Orbit left');
		expect(t.editor.snapshot()).toMatchObject({ nodeId: 'a', label: 'Orbit left', follow: false, edits: 0 });
		expect(t.calls.loadSession).not.toHaveBeenCalled();
	});

	it('refuses a step with no preset', async () => {
		const t = setup({ nodes: { a: makeNode('a', { preset_id: null }) } });
		expect(await t.editor.open('a')).toBe(false);
		expect(t.calls.error).toHaveBeenCalled();
		expect(t.calls.open).not.toHaveBeenCalled();
	});

	it('refuses a followed step whose session is gone', async () => {
		const t = setup({ followed: { b: { status: 'deleted' } } });
		expect(await t.editor.open('b')).toBe(false);
		expect(t.calls.open).not.toHaveBeenCalled();
	});

	it('does not open when the elixir cannot be settled first', async () => {
		const t = setup({ settle: false });
		expect(await t.editor.open('a')).toBe(false);
		expect(t.calls.open).not.toHaveBeenCalled();
		expect(t.calls.error).toHaveBeenCalled();
		expect(t.editor.snapshot().opening).toBe(false);
	});
});

describe('local persistence (Copy and New steps)', () => {
	it('reports edits to the tab dirty dot and back to zero when reverted', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'orbit wider' });
		expect(t.editor.snapshot().edits).toBe(1);
		expect(t.calls.setDirty).toHaveBeenLastCalledWith(1);
		t.edit({ prompt: 'orbit' });
		expect(t.editor.snapshot().edits).toBe(0);
		expect(t.calls.setDirty).toHaveBeenLastCalledWith(0);
	});

	it('never saves a session and never touches the session machinery', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.back();
		await t.editor.answerConfirm('discard');
		expect(t.calls.saveSession).not.toHaveBeenCalled();
		expect(t.calls.loadSession).not.toHaveBeenCalled();
		expect(t.timers).toHaveLength(0);
	});

	it('remembers the baseline so a remounted lane can resume', async () => {
		const t = setup();
		await t.editor.open('a');
		expect(t.calls.setBaseline).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 }));
	});
});

describe('leaving', () => {
	it('leaves straight away when nothing changed', async () => {
		const t = setup();
		await t.editor.open('a');
		await t.editor.back();
		expect(t.calls.close).toHaveBeenCalled();
		expect(t.editor.snapshot().nodeId).toBeNull();
		expect(t.calls.setTitle).toHaveBeenLastCalledWith('Potion study');
		expect(t.calls.setDirty).toHaveBeenLastCalledWith(0);
		expect(t.calls.onReturn).toHaveBeenCalledWith('a', false);
	});

	it('asks before leaving with unsaved edits', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.back();
		expect(t.editor.snapshot().confirm).toEqual({ reason: 'leave', target: null });
		expect(t.calls.close).not.toHaveBeenCalled();
	});

	it('Cancel keeps editing', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.back();
		await t.editor.answerConfirm('cancel');
		expect(t.editor.snapshot().confirm).toBeNull();
		expect(t.editor.snapshot().nodeId).toBe('a');
	});

	it('Discard leaves without saving', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.back();
		await t.editor.answerConfirm('discard');
		expect(t.calls.commit).not.toHaveBeenCalled();
		expect(t.editor.snapshot().nodeId).toBeNull();
		expect(t.calls.onReturn).toHaveBeenCalledWith('a', false);
	});
});

describe('saving to the elixir', () => {
	it('writes the changed settings to the node and returns to the lane flagged as edited', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'orbit wider', formData: { steps: 30 } });
		expect(await t.editor.save()).toBe('saved');
		expect(t.calls.commit).toHaveBeenCalledWith('a', { prompt: 'orbit wider', formData: { steps: 30 } });
		expect(t.calls.onReturn).toHaveBeenCalledWith('a', true);
		expect(t.editor.snapshot().nodeId).toBeNull();
		expect(t.calls.saved).toHaveBeenCalledWith('Saved Orbit left to Potion study', expect.any(Function));
	});

	it('Undo puts the previous settings back', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'orbit wider' });
		await t.editor.save();
		const undo = t.calls.saved.mock.calls[0][1] as () => void;
		undo();
		expect(t.calls.commit).toHaveBeenLastCalledWith('a', {});
	});

	it('skips the write and the toast when nothing changed', async () => {
		const t = setup();
		await t.editor.open('a');
		await t.editor.save();
		expect(t.calls.commit).not.toHaveBeenCalled();
		expect(t.calls.saved).not.toHaveBeenCalled();
		expect(t.calls.onReturn).toHaveBeenCalledWith('a', false);
	});

	it('Save from the unsaved prompt saves then leaves', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.back();
		await t.editor.answerConfirm('save');
		expect(t.calls.commit).toHaveBeenCalledOnce();
		expect(t.editor.snapshot().nodeId).toBeNull();
	});

	it('stays open and reports an error when the write fails', async () => {
		const t = setup({ commit: async () => 'error' });
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		expect(await t.editor.save()).toBe('error');
		expect(t.calls.error).toHaveBeenCalled();
		expect(t.editor.snapshot()).toMatchObject({ nodeId: 'a', saving: false });
	});
});

describe('switching step', () => {
	it('switches at once when clean', async () => {
		const t = setup({ nodes: { a: makeNode('a'), b: makeNode('b', { preset_id: 'krea', mode: 't2i' }) } });
		await t.editor.open('a');
		await t.editor.go('b');
		expect(t.calls.open).toHaveBeenCalledTimes(2);
		expect(t.editor.snapshot().nodeId).toBe('b');
	});

	it('asks first when dirty and opens the target after Discard', async () => {
		const t = setup({ nodes: { a: makeNode('a'), b: makeNode('b', { preset_id: 'krea', mode: 't2i' }) } });
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.go('b');
		expect(t.editor.snapshot().confirm).toEqual({ reason: 'switch', target: 'b' });
		await t.editor.answerConfirm('discard');
		expect(t.editor.snapshot().nodeId).toBe('b');
		expect(t.calls.commit).not.toHaveBeenCalled();
	});

	it('saves and then opens the target after Save', async () => {
		const t = setup({ nodes: { a: makeNode('a'), b: makeNode('b', { preset_id: 'krea', mode: 't2i' }) } });
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		await t.editor.go('b');
		await t.editor.answerConfirm('save');
		expect(t.calls.commit).toHaveBeenCalledWith('a', { prompt: 'changed' });
		expect(t.editor.snapshot().nodeId).toBe('b');
		expect(t.calls.onReturn).not.toHaveBeenCalled();
	});

	it('ignores a switch to the step already open', async () => {
		const t = setup();
		await t.editor.open('a');
		await t.editor.go('a');
		expect(t.calls.open).toHaveBeenCalledOnce();
	});
});

describe('conflicts', () => {
	it('offers Cancel, Reload and Overwrite when the node changed elsewhere', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), a: makeNode('a', { overrides: { prompt: 'theirs' } }) } });
		expect(await t.editor.save()).toBe('conflict');
		expect(t.editor.snapshot().conflict).toMatchObject({ reason: 'changed', canOverwrite: true });
		expect(t.calls.commit).not.toHaveBeenCalled();
	});

	it('saves silently when only another node changed', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), b: makeNode('b', { overrides: { prompt: 'other' } }) } });
		expect(await t.editor.save()).toBe('saved');
		expect(t.calls.adopt).toHaveBeenCalled();
		expect(t.calls.commit).toHaveBeenCalledWith('a', { prompt: 'mine' });
	});

	it('Overwrite applies the edits on top of the newer node', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), a: makeNode('a', { overrides: { formData: { steps: 9 } } }) } });
		await t.editor.save();
		await t.editor.resolveConflict('overwrite');
		expect(t.calls.commit).toHaveBeenCalledWith('a', { prompt: 'mine', formData: { steps: 9 } });
		expect(t.editor.snapshot().nodeId).toBeNull();
	});

	it('Cancel closes the prompt and keeps editing', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), a: makeNode('a', { overrides: { prompt: 'theirs' } }) } });
		await t.editor.save();
		await t.editor.resolveConflict('cancel');
		expect(t.editor.snapshot()).toMatchObject({ conflict: null, nodeId: 'a' });
	});

	it('Reload step reopens the step from the newer node and drops the edits', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), a: makeNode('a', { overrides: { prompt: 'theirs' } }) } });
		await t.editor.save();
		await t.editor.resolveConflict('reload');
		expect(t.calls.adopt).toHaveBeenCalled();
		expect(t.calls.open).toHaveBeenCalledTimes(2);
		expect(t.editor.snapshot()).toMatchObject({ nodeId: 'a', edits: 0, conflict: null });
	});

	it('cannot overwrite when the step now uses another preset', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { ...t.nodes(), a: makeNode('a', { preset_id: 'krea', mode: 't2i' }) } });
		await t.editor.save();
		expect(t.editor.snapshot().conflict?.canOverwrite).toBe(false);
		await t.editor.resolveConflict('overwrite');
		expect(t.calls.commit).not.toHaveBeenCalled();
	});

	it('reports a removed step and leaves on Reload', async () => {
		const t = setup();
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		t.setRemote({ revision: 6, nodes: { b: t.nodes().b } });
		await t.editor.save();
		expect(t.editor.snapshot().conflict).toMatchObject({ reason: 'gone', canOverwrite: false });
		await t.editor.resolveConflict('reload');
		expect(t.editor.snapshot().nodeId).toBeNull();
	});

	it('turns a write-time revision conflict into the same prompt', async () => {
		const t = setup({ commit: async () => 'conflict' });
		await t.editor.open('a');
		t.advance(5000);
		t.edit({ prompt: 'mine' });
		expect(await t.editor.save()).toBe('conflict');
		expect(t.editor.snapshot().conflict).toMatchObject({ canOverwrite: true });
	});
});

describe('follow persistence (session steps)', () => {
	it('opens against the session and loads it into the tab', async () => {
		const t = setup();
		await t.editor.open('b');
		expect(t.calls.open).toHaveBeenCalledWith(expect.objectContaining({ persistence: 'session', selectedSessionId: 's1' }));
		expect(t.calls.loadSession).toHaveBeenCalledOnce();
		expect(t.editor.snapshot()).toMatchObject({ follow: true, sessionName: 'Bottle orbit' });
	});

	it('has no dirty tracking and refuses an elixir save', async () => {
		const t = setup();
		await t.editor.open('b');
		t.advance(5000);
		t.edit({ prompt: 'changed' });
		expect(t.editor.snapshot().edits).toBe(0);
		expect(await t.editor.save()).toBe('noop');
		expect(t.calls.commit).not.toHaveBeenCalled();
	});

	it('saves the session shortly after it turns dirty', async () => {
		const t = setup();
		await t.editor.open('b');
		t.sessionDirty(true);
		expect(t.timers).toHaveLength(1);
		expect(t.timers[0].ms).toBe(1500);
		t.timers[0].run();
		expect(t.calls.saveSession).toHaveBeenCalledWith('emb');
	});

	it('restarts the wait while edits keep coming', async () => {
		const t = setup();
		await t.editor.open('b');
		t.sessionDirty(true);
		t.sessionDirty(true);
		expect(t.timers).toHaveLength(1);
	});

	it('flushes the session on Back and leaves without a prompt', async () => {
		const t = setup();
		await t.editor.open('b');
		await t.editor.back();
		expect(t.calls.saveSession).toHaveBeenCalledWith('emb');
		expect(t.editor.snapshot().nodeId).toBeNull();
		expect(t.editor.snapshot().confirm).toBeNull();
	});

	it('stays when the session cannot be saved', async () => {
		const t = setup();
		await t.editor.open('b');
		t.calls.saveSession.mockResolvedValueOnce(false);
		await t.editor.back();
		expect(t.editor.snapshot().nodeId).toBe('b');
		expect(t.calls.error).toHaveBeenCalled();
	});

	it('flushes before switching to another step', async () => {
		const t = setup({ nodes: { a: makeNode('a'), b: makeNode('b', { session_mode: 'follow', session_ref: { id: 's1' } }) } });
		await t.editor.open('b');
		await t.editor.go('a');
		expect(t.calls.saveSession).toHaveBeenCalled();
		expect(t.editor.snapshot().nodeId).toBe('a');
	});
});

describe('resume after the lane was remounted', () => {
	it('re-attaches to an embedded tab that is still there', async () => {
		const t = setup();
		await t.editor.open('a');
		t.editor.destroy();
		expect(t.editor.snapshot().nodeId).toBeNull();
		expect(await t.editor.resume()).toBe(true);
		expect(t.editor.snapshot()).toMatchObject({ nodeId: 'a', label: 'Orbit left' });
		expect(t.calls.setTitle).toHaveBeenLastCalledWith('Potion study › Orbit left');
	});

	it('does nothing when there is no embedded tab', async () => {
		const t = setup();
		expect(await t.editor.resume()).toBe(false);
	});
});
