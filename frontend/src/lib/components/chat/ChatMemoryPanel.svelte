<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import Button from '$lib/components/ui/Button.svelte';
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { MemoryNote, MemoryScope } from '$lib/types/chat';

	// The panel resolves the preset name + active model itself from the chat's
	// current tab context (see UnifiedAIChat). It needs the raw preset ULID and
	// the tab's form_data (to map the active checkpoint path -> model ULID).
	export let presetId: string | null = null;
	export let formData: Record<string, any> = {};
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

	$: groups = [
		{ scope: 'global' as MemoryScope, ref: null as string | null, available: true },
		{ scope: 'preset' as MemoryScope, ref: presetId, available: !!presetId },
		{ scope: 'model' as MemoryScope, ref: modelId, available: !!modelId }
	];

	// Notes per scope group, keyed by `group.scope` (the `{#each}` below is keyed by
	// `group.scope`). `notes` starts empty and is populated asynchronously by
	// loadNotes() after mount; this is a `$:` statement (not a plain function called
	// from `{@const}`) so Svelte's dependency scan sees `notes` directly and
	// recomputes once it loads — a function call hides that read, and since the
	// group rows render (and get their key assigned) before the fetch resolves, the
	// panel would keep showing "no notes" forever.
	$: notesByGroupScope = new Map(
		groups.map((g) => [
			g.scope,
			notes.filter((n) => n.scope === g.scope && (g.scope === 'global' || n.scope_ref === g.ref))
		])
	);

	// Notes relevant to this conversation (global + the active preset/model),
	// as opposed to every note the user has ever written across every preset —
	// what the summary row and the composer's "Memory · n" badge both mean by
	// "active".
	$: activeNoteCount = Array.from(notesByGroupScope.values()).reduce(
		(sum, list) => sum + list.length,
		0
	);

	$: if (!loading) onCountChange?.(activeNoteCount);

	function groupTitle(group: { scope: MemoryScope; available: boolean }, count: number): string {
		if (group.scope === 'global') return `Global · ${count} note${count === 1 ? '' : 's'}`;
		if (group.scope === 'preset') return presetName ? `Preset · ${presetName}` : 'Preset';
		return modelName ? `Model · ${modelName}` : 'Model';
	}

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

<!-- Docked inspector: a plain flex child of the conversation area (mounted and
     positioned by UnifiedAIChat per the chat-rework brief's Seam 2), not an
     overlay — no portal, no backdrop, no fixed positioning of its own. -->
<div
	class="memory-panel-in flex h-full w-[332px] flex-shrink-0 flex-col overflow-hidden border-l border-line-strong bg-surface-2"
	role="region"
	aria-label="Memory"
>
	<!-- Head -->
	<div class="flex flex-shrink-0 items-center gap-2.5 border-b border-line px-4 py-3">
		<svg class="h-4 w-4 flex-shrink-0 text-signal" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.5 2A2.5 2.5 0 0112 4.5v15a2.5 2.5 0 01-4.9.7A2.5 2.5 0 013.5 17a2.5 2.5 0 01-.5-4.9A2.5 2.5 0 013 7.5 2.5 2.5 0 015.6 3.4 2.5 2.5 0 019.5 2zM14.5 2A2.5 2.5 0 0012 4.5v15a2.5 2.5 0 004.9.7A2.5 2.5 0 0020.5 17a2.5 2.5 0 00.5-4.9A2.5 2.5 0 0021 7.5a2.5 2.5 0 00-2.6-4.1A2.5 2.5 0 0014.5 2z" />
		</svg>
		<div class="min-w-0 flex-1">
			<h2 class="truncate text-sm font-semibold text-fg">Memory</h2>
			<div class="mt-0.5 truncate text-2xs text-fg-subtle">What Potion AI carries into replies</div>
		</div>
		<Tooltip text="Close" position="left" delay={150}>
			<IconButton icon="close" label="Close" size="sm" onclick={onClose} />
		</Tooltip>
	</div>

	<!-- Body -->
	<div
		bind:this={scrollEl}
		class="flex-1 space-y-4 overflow-y-auto p-3 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent"
	>
		{#if error}
			<div class="rounded border border-danger/25 bg-surface-1 px-3 py-2 text-xs text-danger">
				{error}
			</div>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-10">
				<Spinner />
			</div>
		{:else}
			<div class="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-1 px-2.5 py-2">
				<span class="text-xs text-fg-muted"
					>{activeNoteCount} active note{activeNoteCount === 1 ? '' : 's'}</span
				>
				<span
					class="font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle tabular-nums"
					title="Injected into every chat message"
				>
					~{totalFootprint.tokens.toLocaleString()} tokens in context
				</span>
			</div>

			{#each groups as group (group.scope)}
				{@const groupNotes = notesByGroupScope.get(group.scope) ?? []}
				{@const footprint = footprintByScope.get(group.scope)}
				<section>
					<div class="mb-1.5 flex items-center gap-2 px-0.5">
						<h3 class="truncate font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
							{groupTitle(group, groupNotes.length)}
						</h3>
						{#if footprint && footprint.overCap}
							<Badge variant="warning" size="sm"
								>{footprint.injectedCount} of {footprint.total} injected</Badge
							>
						{/if}
						{#if group.available}
							<button
								type="button"
								class="ml-auto flex-shrink-0 text-2xs font-medium text-signal transition-opacity hover:opacity-80"
								on:click={() => startAdd(group.scope)}
							>
								+ Add
							</button>
						{/if}
					</div>

					{#if !group.available}
						<div class="rounded-lg border border-dashed border-line px-3 py-3 text-center text-2xs text-fg-subtle">
							{group.scope === 'model' ? 'No active model' : 'No active preset'}
						</div>
					{:else}
						<!-- Add form -->
						{#if addingScope === group.scope}
							<div class="mb-2 space-y-2 rounded-lg border border-line bg-surface-1 p-2.5">
								<Input bind:value={addKey} placeholder="Key (e.g. tone)" class="text-xs" />
								<textarea
									bind:value={addContent}
									placeholder="What to remember…"
									rows="2"
									class="input w-full resize-y text-xs"
								></textarea>
								<div class="flex items-center justify-end gap-2">
									<Button variant="ghost" size="xs" onclick={cancelAdd}>Cancel</Button>
									<Button
										variant="primary"
										size="xs"
										disabled={saving || !addKey.trim() || !addContent.trim()}
										onclick={() => submitAdd(group.scope, group.ref)}
									>
										Save
									</Button>
								</div>
							</div>
						{/if}

						<!-- Notes -->
						{#if groupNotes.length === 0 && addingScope !== group.scope}
							<div class="rounded-lg border border-dashed border-line px-3 py-3 text-center text-2xs text-fg-subtle">
								Nothing remembered yet
							</div>
						{:else}
							<div class="space-y-1.5">
								{#each groupNotes as note (note.id)}
									{@const notInjected = !!footprint && !footprint.injectedIds.has(note.id)}
									<div class="rounded-lg border border-line bg-surface-1 p-2.5 {notInjected ? 'opacity-60' : ''}">
										{#if editingId === note.id}
											<div class="space-y-2">
												<Input bind:value={editKey} placeholder="Key" class="text-xs" />
												<textarea
													bind:value={editContent}
													rows="2"
													class="input w-full resize-y text-xs"
												></textarea>
												<div class="flex items-center justify-end gap-2">
													<Button variant="ghost" size="xs" onclick={cancelEdit}>Cancel</Button>
													<Button
														variant="primary"
														size="xs"
														disabled={saving || !editKey.trim() || !editContent.trim()}
														onclick={() => submitEdit(note.id)}
													>
														Save
													</Button>
												</div>
											</div>
										{:else}
											<div class="flex items-start gap-2">
												<div class="min-w-0 flex-1">
													<div class="flex items-center gap-1.5">
														<span class="truncate font-mono text-2xs font-semibold text-fg">{note.key}</span>
														{#if note.updated_at}
															<span class="flex-shrink-0 font-mono text-2xs tabular-nums text-fg-subtle"
																>{formatTimestamp(note.updated_at)}</span
															>
														{/if}
														{#if notInjected}
															<Badge variant="neutral" size="sm">not injected</Badge>
														{/if}
													</div>
													<div class="mt-1 whitespace-pre-wrap break-words text-xs text-fg-muted">
														{note.content}
													</div>
												</div>
												<div class="flex flex-shrink-0 items-center gap-0.5">
													{#if confirmDeleteId === note.id}
														<Tooltip text="Confirm delete" position="left" delay={150}>
															<IconButton
																icon="check"
																label="Confirm delete"
																size="sm"
																class="text-danger"
																onclick={() => deleteNote(note.id)}
															/>
														</Tooltip>
														<Tooltip text="Cancel" position="left" delay={150}>
															<IconButton
																icon="close"
																label="Cancel"
																size="sm"
																onclick={() => (confirmDeleteId = null)}
															/>
														</Tooltip>
													{:else}
														<Tooltip text="Edit" position="left" delay={150}>
															<IconButton icon="edit" label="Edit" size="sm" onclick={() => startEdit(note)} />
														</Tooltip>
														<Tooltip text="Delete" position="left" delay={150}>
															<IconButton
																icon="trash"
																label="Delete"
																size="sm"
																class="hover:text-danger"
																onclick={() => {
																	cancelEdit();
																	confirmDeleteId = note.id;
																}}
															/>
														</Tooltip>
													{/if}
												</div>
											</div>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					{/if}
				</section>
			{/each}
		{/if}
	</div>

	<!-- Footer -->
	<div class="flex-shrink-0 border-t border-line px-3 py-2.5">
		<Button variant="primary" size="sm" icon="plus" class="w-full shadow-raised" onclick={startAddFromFooter}>
			New memory note
		</Button>
	</div>
</div>

<style>
	@keyframes memory-panel-in {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}

	.memory-panel-in {
		animation: memory-panel-in 160ms ease-out;
	}
</style>
