<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import type { MemoryNote, MemoryScope } from '$lib/types/chat';
	import { buildMemoryGroups, memoryGroupTitle, notesForGroup } from '$lib/chat/memoryGroups';

	// The panel resolves the preset name + active model itself from the chat's
	// current tab context (see UnifiedAIChat). It needs the raw preset ULID and
	// the tab's form_data (to map the active checkpoint path -> model ULID).
	export let presetId: string | null = null;
	export let formData: Record<string, any> = {};
	// The chat session's mode (e.g. "generation", "lora-dataset") and its
	// display name — the "This mode" group's scope_ref and label. Unlike
	// preset/model, a session's mode is always set (see chatSession.ts's
	// DEFAULT_CHAT_MODE), so this group is always available.
	export let modeId: string | null = null;
	export let modeLabel: string | null = null;
	export let onClose: () => void;
	// Lets the composer's "Memory" button show a live count without this panel
	// needing to be mounted just to read one.
	export let onCountChange: ((n: number) => void) | undefined = undefined;

	// Resolved display context
	let presetName: string | null = null;
	let modelId: string | null = null;
	let modelName: string | null = null;

	// Notes + status
	let notes: MemoryNote[] = [];
	let loading = true;
	let error: string | null = null;

	// Per-group injection caps (same numbers ChatContextBuilder.inject_memory_block
	// applies server-side), served alongside the notes so the footprint math below
	// stays in lockstep with what's actually injected — never re-derived locally.
	let injection: { cap_per_group: number; max_content_len: number } | null = null;

	// Per-group "add note" form state
	let addingScope: MemoryScope | null = null;
	let addKey = '';
	let addContent = '';
	let saving = false;

	// Inline edit state (by note id)
	let editingId: string | null = null;
	let editKey = '';
	let editContent = '';

	// Two-click inline delete confirm (by note id)
	let confirmDeleteId: string | null = null;

	let scrollEl: HTMLDivElement;

	$: groups = buildMemoryGroups({ presetId, modelId, modeId });

	// Notes per scope group, keyed by `group.scope` (the `{#each}` below is keyed by
	// `group.scope`). `notes` starts empty and is populated asynchronously by
	// loadNotes() after mount; this is a `$:` statement (not a plain function called
	// from `{@const}`) so Svelte's dependency scan sees `notes` directly and
	// recomputes once it loads — a function call hides that read, and since the
	// group rows render (and get their key assigned) before the fetch resolves, the
	// panel would keep showing "no notes" forever.
	$: notesByGroupScope = new Map(groups.map((g) => [g.scope, notesForGroup(notes, g)]));

	// Notes relevant to this conversation (global + the active preset/model),
	// as opposed to every note the user has ever written across every preset —
	// what the summary row and the composer's "Memory · n" badge both mean by
	// "active".
	$: activeNoteCount = Array.from(notesByGroupScope.values()).reduce(
		(sum, list) => sum + list.length,
		0
	);

	$: if (!loading) onCountChange?.(activeNoteCount);

	interface GroupFootprint {
		total: number;
		injectedCount: number;
		chars: number;
		tokens: number;
		overCap: boolean;
		injectedIds: Set<string>;
	}

	// Same order the backend injects in (repository.list_notes ORDER BY
	// updated_at DESC) — re-sorted here rather than trusted from API order so
	// the "beyond cap" marking stays correct even if that ever changes.
	function groupFootprint(groupNotes: MemoryNote[]): GroupFootprint {
		const cap = injection?.cap_per_group ?? groupNotes.length;
		const maxLen = injection?.max_content_len ?? Infinity;
		const sorted = [...groupNotes].sort((a, b) =>
			(b.updated_at || '').localeCompare(a.updated_at || '')
		);
		const injected = sorted.slice(0, cap);
		const chars = injected.reduce((sum, n) => sum + Math.min(n.content.length, maxLen), 0);
		return {
			total: groupNotes.length,
			injectedCount: injected.length,
			chars,
			tokens: Math.floor(chars / 4),
			overCap: groupNotes.length > cap,
			injectedIds: new Set(injected.map((n) => n.id))
		};
	}

	$: footprintByScope = new Map(
		groups.map((g) => [g.scope, groupFootprint(notesByGroupScope.get(g.scope) ?? [])])
	);

	$: totalFootprint = Array.from(footprintByScope.values()).reduce(
		(acc, f) => ({
			notes: acc.notes + f.injectedCount,
			chars: acc.chars + f.chars,
			tokens: acc.tokens + f.tokens
		}),
		{ notes: 0, chars: 0, tokens: 0 }
	);

	onMount(() => {
		resolveContext();
		loadNotes();
	});

	async function loadNotes() {
		loading = true;
		error = null;
		try {
			const response = await api.listMemory({});
			if (response.success) {
				notes = response.data?.notes || [];
				injection = response.data?.injection || null;
			} else {
				error = response.message || response.error || 'Failed to load memory';
			}
		} catch (err: any) {
			logger.error('Failed to load memory notes:', err);
			error = err?.message || 'Failed to load memory';
		} finally {
			loading = false;
		}
	}

	// Resolve the preset name and the active model (ULID + display name).
	// Model resolution mirrors ModelField: scan form_data for the first model
	// field value (shape {modelPath}), then look the file path up via getModels.
	async function resolveContext() {
		try {
			if (presetId) {
				const response = await api.listPresets();
				if (response.success) {
					const match = (response.data || []).find((p) => p.id === presetId);
					presetName = match?.name || null;
				}
			}
		} catch (err) {
			logger.error('Failed to resolve preset name:', err);
		}

		try {
			const modelPath = findActiveModelPath(formData);
			if (modelPath) {
				const filename = modelPath.split('/').pop() || modelPath;
				const response = await api.getModels({ search: filename, limit: 10 });
				if (response.success && response.data?.models) {
					const found = response.data.models.find((m: any) => m.file_path === modelPath);
					if (found) {
						modelId = found.id;
						modelName = found.custom_name || found.providers?.[0]?.name || found.filename || filename;
					}
				}
			}
		} catch (err) {
			logger.error('Failed to resolve active model:', err);
		}
	}

	// First form_data value shaped like a model field ({modelPath: string}).
	// LoRA pickers use {model, strength} so they are ignored; this picks the
	// primary checkpoint-style field.
	function findActiveModelPath(data: Record<string, any>): string | null {
		for (const value of Object.values(data || {})) {
			if (value && typeof value === 'object' && typeof (value as any).modelPath === 'string') {
				const path = (value as any).modelPath.trim();
				if (path) return path;
			}
		}
		return null;
	}

	function startAdd(scope: MemoryScope) {
		cancelEdit();
		addingScope = scope;
		addKey = '';
		addContent = '';
		error = null;
	}

	function cancelAdd() {
		addingScope = null;
		addKey = '';
		addContent = '';
	}

	// Entry point for the footer's generic "+ New memory note" — the per-group
	// "+ Add" affordances already exist for a scoped note, so this defaults to
	// the group most notes belong in and scrolls it into view.
	function startAddFromFooter() {
		startAdd('global');
		scrollEl?.scrollTo({ top: 0, behavior: 'smooth' });
	}

	async function submitAdd(scope: MemoryScope, ref: string | null) {
		if (!addKey.trim() || !addContent.trim() || saving) return;
		saving = true;
		error = null;
		try {
			const response = await api.createMemory({
				key: addKey.trim(),
				content: addContent.trim(),
				scope,
				scope_ref: ref
			});
			if (response.success) {
				cancelAdd();
				await loadNotes();
			} else {
				error = response.message || response.error || 'Failed to save note';
			}
		} catch (err: any) {
			logger.error('Failed to create memory note:', err);
			error = err?.message || 'Failed to save note';
		} finally {
			saving = false;
		}
	}

	function startEdit(note: MemoryNote) {
		cancelAdd();
		confirmDeleteId = null;
		editingId = note.id;
		editKey = note.key;
		editContent = note.content;
		error = null;
	}

	function cancelEdit() {
		editingId = null;
		editKey = '';
		editContent = '';
	}

	async function submitEdit(noteId: string) {
		if (!editKey.trim() || !editContent.trim() || saving) return;
		saving = true;
		error = null;
		try {
			const response = await api.updateMemory(noteId, {
				key: editKey.trim(),
				content: editContent.trim()
			});
			if (response.success) {
				cancelEdit();
				await loadNotes();
			} else {
				error = response.message || response.error || 'Failed to update note';
			}
		} catch (err: any) {
			logger.error('Failed to update memory note:', err);
			error = err?.message || 'Failed to update note';
		} finally {
			saving = false;
		}
	}

	async function deleteNote(noteId: string) {
		saving = true;
		error = null;
		try {
			const response = await api.deleteMemory(noteId);
			if (response.success) {
				confirmDeleteId = null;
				await loadNotes();
			} else {
				error = response.message || response.error || 'Failed to delete note';
			}
		} catch (err: any) {
			logger.error('Failed to delete memory note:', err);
			error = err?.message || 'Failed to delete note';
		} finally {
			saving = false;
		}
	}

	function formatTimestamp(iso: string | null): string {
		if (!iso) return '';
		const date = new Date(iso);
		if (isNaN(date.getTime())) return '';
		return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}
</script>

<!-- Backdrop + inspector: positioned by their own CSS (chat-concept.css,
     .memory-backdrop / .memory-inspector), absolute inside .conversation —
     mounted here as flat siblings, last children of .conversation. -->
<button class="memory-backdrop" aria-label="Close memory" on:click={onClose}></button>
<aside class="memory-inspector" aria-label="Memory">
	<div class="memory-head">
		<svg class="icon"><use href="#i-memory" /></svg>
		<div class="memory-title">
			<strong>Memory</strong>
			<span>What PotionAI carries into replies</span>
		</div>
		<button class="tiny-button" aria-label="Close" title="Close" on:click={onClose}>
			<svg class="icon"><use href="#i-close" /></svg>
		</button>
	</div>

	<div class="memory-scroll" bind:this={scrollEl}>
		{#if error}
			<div class="memory-error">{error}</div>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-10">
				<Spinner />
			</div>
		{:else}
			<div class="memory-summary">
				<span>{activeNoteCount} active note{activeNoteCount === 1 ? '' : 's'}</span>
				<span title="Injected into every chat message"
					>~{totalFootprint.tokens.toLocaleString()} tokens in context</span
				>
			</div>

			{#each groups as group (group.scope)}
				{@const groupNotes = notesByGroupScope.get(group.scope) ?? []}
				{@const footprint = footprintByScope.get(group.scope)}
				<section class="memory-group">
					<div class="memory-group-head">
						<span>
							{memoryGroupTitle(group, groupNotes.length, { presetName, modelName, modeLabel })}
							{#if footprint && footprint.overCap}
								<span class="memory-footprint-badge">{footprint.injectedCount}/{footprint.total} injected</span>
							{/if}
						</span>
						{#if group.available}
							<button type="button" data-memory-action="Add note" on:click={() => startAdd(group.scope)}>
								+ Add
							</button>
						{/if}
					</div>

					{#if !group.available}
						<div class="memory-empty">
							{group.scope === 'model'
								? 'No active model'
								: group.scope === 'mode'
									? 'No active mode'
									: 'No active preset'}
						</div>
					{:else}
						{#if addingScope === group.scope}
							<div class="memory-add-form">
								<input type="text" bind:value={addKey} placeholder="Key (e.g. tone)" />
								<textarea bind:value={addContent} placeholder="What to remember…" rows="2"></textarea>
								<div class="memory-add-form-actions">
									<button type="button" class="cancel" on:click={cancelAdd}>Cancel</button>
									<button
										type="button"
										class="save"
										disabled={saving || !addKey.trim() || !addContent.trim()}
										on:click={() => submitAdd(group.scope, group.ref)}
									>
										Save
									</button>
								</div>
							</div>
						{/if}

						{#if groupNotes.length === 0 && addingScope !== group.scope}
							<div class="memory-empty">Nothing remembered yet</div>
						{:else}
							{#each groupNotes as note (note.id)}
								{@const notInjected = !!footprint && !footprint.injectedIds.has(note.id)}
								<div class="memory-note" class:opacity-60={notInjected}>
									{#if editingId === note.id}
										<div class="memory-add-form">
											<input type="text" bind:value={editKey} placeholder="Key" />
											<textarea bind:value={editContent} rows="2"></textarea>
											<div class="memory-add-form-actions">
												<button type="button" class="cancel" on:click={cancelEdit}>Cancel</button>
												<button
													type="button"
													class="save"
													disabled={saving || !editKey.trim() || !editContent.trim()}
													on:click={() => submitEdit(note.id)}
												>
													Save
												</button>
											</div>
										</div>
									{:else}
										<div class="memory-note-head">
											<strong>{note.key}</strong>
											{#if note.updated_at}<span>{formatTimestamp(note.updated_at)}</span>{/if}
											{#if notInjected}<span title="Over the injection cap for this group">not injected</span>{/if}
											<div class="memory-note-actions">
												{#if confirmDeleteId === note.id}
													<button
														type="button"
														class="danger"
														title="Confirm delete"
														aria-label="Confirm delete"
														on:click={() => deleteNote(note.id)}
													>
														<svg class="icon"><use href="#i-check" /></svg>
													</button>
													<button
														type="button"
														title="Cancel"
														aria-label="Cancel"
														on:click={() => (confirmDeleteId = null)}
													>
														<svg class="icon"><use href="#i-close" /></svg>
													</button>
												{:else}
													<button type="button" title="Edit" aria-label="Edit" on:click={() => startEdit(note)}>
														<svg class="icon"><use href="#i-edit" /></svg>
													</button>
													<button
														type="button"
														class="danger"
														title="Delete"
														aria-label="Delete"
														on:click={() => {
															cancelEdit();
															confirmDeleteId = note.id;
														}}
													>
														<svg class="icon"><use href="#i-trash" /></svg>
													</button>
												{/if}
											</div>
										</div>
										<p>{note.content}</p>
									{/if}
								</div>
							{/each}
						{/if}
					{/if}
				</section>
			{/each}
		{/if}
	</div>

	<div class="memory-footer">
		<button type="button" data-memory-action="Create memory note" on:click={startAddFromFooter}>
			+ New memory note
		</button>
	</div>
</aside>
