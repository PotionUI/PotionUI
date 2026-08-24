<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import Button from '$lib/components/ui/Button.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import type { MemoryNote, MemoryScope } from '$lib/types/chat';

	// The panel resolves the preset name + active model itself from the chat's
	// current tab context (see UnifiedAIChat). It needs the raw preset ULID and
	// the tab's form_data (to map the active checkpoint path -> model ULID).
	export let presetId: string | null = null;
	export let formData: Record<string, any> = {};
	export let onClose: () => void;

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

	$: groups = [
		{ scope: 'global' as MemoryScope, label: 'Global', ref: null as string | null, available: true },
		{
			scope: 'preset' as MemoryScope,
			label: presetName ? `Preset: ${presetName}` : 'Preset',
			ref: presetId,
			available: !!presetId
		},
		{
			scope: 'model' as MemoryScope,
			label: modelName ? `Model: ${modelName}` : 'Model',
			ref: modelId,
			available: !!modelId
		}
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

<!-- Backdrop -->
<div
	class="fixed inset-0 z-40"
	role="button"
	tabindex="-1"
	aria-label="Close memory panel"
	on:click={onClose}
	on:keydown={(e) => { if (e.key === 'Escape') onClose(); }}
></div>

<!-- Slide-out panel -->
<div
	class="fixed top-2 right-2 bottom-2 z-50 w-[92vw] max-w-[400px] flex flex-col bg-surface-1 border border-line rounded-xl shadow-overlay overflow-hidden"
	role="dialog"
	aria-label="Memory"
>
	<!-- Header -->
	<div class="flex items-center gap-2 px-4 py-3 border-b border-line flex-shrink-0">
		<svg class="w-4 h-4 text-signal flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.5 2A2.5 2.5 0 0112 4.5v15a2.5 2.5 0 01-4.9.7A2.5 2.5 0 013.5 17a2.5 2.5 0 01-.5-4.9A2.5 2.5 0 013 7.5 2.5 2.5 0 015.6 3.4 2.5 2.5 0 019.5 2zM14.5 2A2.5 2.5 0 0012 4.5v15a2.5 2.5 0 004.9.7A2.5 2.5 0 0020.5 17a2.5 2.5 0 00.5-4.9A2.5 2.5 0 0021 7.5a2.5 2.5 0 00-2.6-4.1A2.5 2.5 0 0014.5 2z" />
		</svg>
		<h2 class="text-sm font-semibold text-fg">Memory</h2>
		<button
			type="button"
			title="Close"
			class="ml-auto p-1.5 text-fg-subtle hover:text-fg-muted hover:bg-surface-2 rounded transition-colors"
			on:click={onClose}
		>
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
			</svg>
		</button>
	</div>

	<!-- Body -->
	<div class="flex-1 overflow-y-auto p-3 space-y-4 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent">
		{#if error}
			<div class="bg-surface-2 border border-danger/25 rounded px-3 py-2 text-xs text-danger">
				{error}
			</div>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-10">
				<Spinner />
			</div>
		{:else}
			{#if injection && totalFootprint.notes > 0}
				<div
					class="px-0.5 font-mono text-2xs tabular-nums text-fg-subtle"
					title="Injected into every chat message"
				>
					~{totalFootprint.chars.toLocaleString()} chars · ~{totalFootprint.tokens.toLocaleString()} tok
				</div>
			{/if}
			{#each groups as group (group.scope)}
				{@const groupNotes = notesByGroupScope.get(group.scope) ?? []}
				{@const footprint = footprintByScope.get(group.scope)}
				<section>
					<div class="flex items-center gap-2 mb-1.5 px-0.5">
						<h3 class="text-xs font-semibold text-fg-muted truncate">{group.label}</h3>
						<span
							class="font-mono text-2xs text-fg-subtle tabular-nums"
							title={footprint && footprint.total > 0 ? 'Injected into every chat message' : undefined}
							>{groupNotes.length}{footprint && footprint.total > 0
								? ` · ~${footprint.chars.toLocaleString()} chars`
								: ''}</span
						>
						{#if footprint && footprint.overCap}
							<Badge variant="warning" size="sm"
								>{footprint.injectedCount} of {footprint.total} injected</Badge
							>
						{/if}
						{#if group.available}
							<button
								type="button"
								title="Add note"
								class="ml-auto p-1 text-fg-subtle hover:text-fg-muted hover:bg-surface-2 rounded transition-colors flex-shrink-0"
								on:click={() => startAdd(group.scope)}
							>
								<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
								</svg>
							</button>
						{/if}
					</div>

					{#if !group.available}
						<div class="text-xs text-fg-subtle px-0.5 py-1.5">
							{group.scope === 'model' ? 'No active model' : 'No active preset'}
						</div>
					{:else}
						<!-- Add form -->
						{#if addingScope === group.scope}
							<div class="bg-surface-2 border border-line rounded-lg p-2.5 mb-2 space-y-2">
								<Input bind:value={addKey} placeholder="Key (e.g. tone)" class="text-xs" />
								<textarea
									bind:value={addContent}
									placeholder="What to remember…"
									rows="2"
									class="input text-xs w-full resize-y"
								></textarea>
								<div class="flex items-center gap-2 justify-end">
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
							<div class="text-xs text-fg-subtle px-0.5 py-1.5">Nothing remembered yet</div>
						{:else}
							<div class="space-y-1.5">
								{#each groupNotes as note (note.id)}
									{@const notInjected = !!footprint && !footprint.injectedIds.has(note.id)}
									<div class="bg-surface-2 border border-line rounded-lg p-2.5 {notInjected ? 'opacity-60' : ''}">
										{#if editingId === note.id}
											<div class="space-y-2">
												<Input bind:value={editKey} placeholder="Key" class="text-xs" />
												<textarea
													bind:value={editContent}
													rows="2"
													class="input text-xs w-full resize-y"
												></textarea>
												<div class="flex items-center gap-2 justify-end">
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
												<div class="flex-1 min-w-0">
													<div class="flex items-center gap-1.5">
														<div class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle truncate">
															{note.key}
														</div>
														{#if notInjected}
															<Badge variant="neutral" size="sm">not injected</Badge>
														{/if}
													</div>
													<div class="text-xs text-fg-muted mt-0.5 whitespace-pre-wrap break-words">
														{note.content}
													</div>
												</div>
												<div class="flex items-center gap-0.5 flex-shrink-0">
													{#if confirmDeleteId === note.id}
														<button
															type="button"
															title="Confirm delete"
															class="p-1 text-danger hover:bg-surface-3 rounded transition-colors"
															on:click={() => deleteNote(note.id)}
														>
															<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
															</svg>
														</button>
														<button
															type="button"
															title="Cancel"
															class="p-1 text-fg-subtle hover:text-fg-muted hover:bg-surface-3 rounded transition-colors"
															on:click={() => (confirmDeleteId = null)}
														>
															<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
															</svg>
														</button>
													{:else}
														<button
															type="button"
															title="Edit"
															class="p-1 text-fg-subtle hover:text-fg-muted hover:bg-surface-3 rounded transition-colors"
															on:click={() => startEdit(note)}
														>
															<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
															</svg>
														</button>
														<button
															type="button"
															title="Delete"
															class="p-1 text-fg-subtle hover:text-danger hover:bg-surface-3 rounded transition-colors"
															on:click={() => { cancelEdit(); confirmDeleteId = note.id; }}
														>
															<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
															</svg>
														</button>
													{/if}
												</div>
											</div>
											{#if note.updated_at}
												<div class="font-mono text-2xs text-fg-subtle tabular-nums mt-1.5">
													{formatTimestamp(note.updated_at)}
												</div>
											{/if}
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
</div>
