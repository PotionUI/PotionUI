<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { keybindingsStore, isHelpOpen, keybindingsByCategory, formatKeyCombo, type KeybindingAction } from '$lib/stores/keybindings';
	import { suppressKeyboard, resumeKeyboard } from '$lib/services/keyboard';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';

	let searchQuery = '';
	let recordingActionId: string | null = null;
	let searchInput: HTMLInputElement;
	let listContainer: HTMLDivElement;

	$: if ($isHelpOpen) {
		suppressKeyboard();
	} else {
		resumeKeyboard();
		searchQuery = '';
		recordingActionId = null;
	}

	$: filteredCategories = getFilteredCategories($keybindingsByCategory, searchQuery);

	function getFilteredCategories(
		categories: Record<string, KeybindingAction[]>,
		query: string
	): Record<string, KeybindingAction[]> {
		if (!query) return categories;
		const lowerQuery = query.toLowerCase();
		const result: Record<string, KeybindingAction[]> = {};
		for (const [cat, bindings] of Object.entries(categories)) {
			const filtered = bindings.filter(
				(b) =>
					b.label.toLowerCase().includes(lowerQuery) ||
					(b.description && b.description.toLowerCase().includes(lowerQuery)) ||
					(b.key && b.key.toLowerCase().includes(lowerQuery))
			);
			if (filtered.length > 0) {
				result[cat] = filtered;
			}
		}
		return result;
	}

	function handleClose() {
		keybindingsStore.closeHelp();
	}

	function handleGlobalKeydown(e: KeyboardEvent) {
		if (!$isHelpOpen) return;

		if (recordingActionId) {
			e.preventDefault();
			e.stopPropagation();

			if (e.key === 'Escape') {
				recordingActionId = null;
				return;
			}

			// Ignore bare modifier keys
			if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;

			const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

			// For non-letter single characters (like ?, !, @), shift is implicit
			const isImplicitShift =
				e.key.length === 1 && e.shiftKey && !/^[a-zA-Z]$/.test(e.key);

			const modifiers = [
				e.ctrlKey && 'ctrl',
				e.shiftKey && !isImplicitShift && 'shift',
				e.altKey && 'alt',
				e.metaKey && 'meta'
			]
				.filter(Boolean)
				.join(',');
			keybindingsStore.updateBinding(recordingActionId, key, modifiers);
			recordingActionId = null;
			return;
		}

		if (e.key === 'Escape') {
			e.preventDefault();
			handleClose();
		}
	}

	function startRecording(actionId: string) {
		recordingActionId = actionId;
	}

	function resetBinding(actionId: string) {
		keybindingsStore.resetBinding(actionId);
	}

	function resetAll() {
		keybindingsStore.resetAll();
	}

	function toggleEnabled(binding: KeybindingAction) {
		keybindingsStore.updateBinding(
			binding.actionId,
			binding.enabled ? null : binding.key,
			binding.modifiers
		);
	}

	$: hasBindings = Object.keys($keybindingsByCategory).length > 0;
	$: hasFilteredResults = Object.keys(filteredCategories).length > 0;

	onMount(() => {
		document.addEventListener('keydown', handleGlobalKeydown, true);
	});

	onDestroy(() => {
		document.removeEventListener('keydown', handleGlobalKeydown, true);
		resumeKeyboard();
	});
</script>

<BaseModal
	isOpen={$isHelpOpen}
	title="Keyboard Shortcuts"
	subtitle="Customize your keybindings"
	sizeClass="md:max-w-2xl md:w-full"
	handleEscapeKey={false}
	on:close={handleClose}
>
	<div class="h-full flex flex-col">
		<!-- Search -->
		<div class="px-5 py-3 border-b border-line/50 flex-shrink-0">
			<div class="relative flex items-center">
				<svg
					class="absolute left-3 w-4 h-4 text-fg-subtle pointer-events-none"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
					/>
				</svg>
				<input
					bind:this={searchInput}
					bind:value={searchQuery}
					type="text"
					placeholder="Filter shortcuts..."
					class="input pl-9"
					data-autofocus
				/>
				{#if searchQuery}
					<button
						type="button"
						on:click={() => (searchQuery = '')}
						class="absolute right-2 p-1 hover:bg-surface-3 rounded text-fg-subtle hover:text-fg-muted"
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				{/if}
			</div>
		</div>

		<!-- Bindings List -->
		<div bind:this={listContainer} class="flex-1 overflow-y-auto min-h-0">
			{#if !hasBindings}
					<div class="px-5 py-12 text-center text-fg-subtle text-sm">
						No keybindings loaded. Check your connection.
					</div>
				{:else if !hasFilteredResults}
					<div class="px-5 py-12 text-center text-fg-subtle text-sm">
						No shortcuts match "{searchQuery}"
					</div>
				{:else}
					{#each Object.entries(filteredCategories) as [category, bindings]}
						<!-- Category Header -->
						<div class="text-xs font-semibold text-fg-subtle uppercase tracking-wider px-5 py-2 bg-surface-2/50 sticky top-0">
							{category}
						</div>

						{#each bindings as binding (binding.actionId)}
							<div
								class="px-5 py-2.5 flex items-center gap-3 transition-colors
									{recordingActionId === binding.actionId
									? 'ring-2 ring-signal/50 bg-signal/5'
									: 'hover:bg-surface-2/50'}"
							>
								<!-- Custom indicator -->
								<div class="w-2 flex-shrink-0">
									{#if binding.isCustom}
										<div class="w-2 h-2 rounded-full bg-signal" title="Customized"></div>
									{/if}
								</div>

								<!-- Label & description -->
								<div class="flex-1 min-w-0">
									<div class="text-sm text-fg">{binding.label}</div>
									{#if binding.description}
										<div class="text-xs text-fg-subtle truncate">{binding.description}</div>
									{/if}
								</div>

								<!-- Key combo or recording state -->
								<div class="flex items-center gap-2 flex-shrink-0">
									{#if recordingActionId === binding.actionId}
										<span class="text-xs text-signal animate-pulse">Press a key combination...</span>
									{:else if binding.key && binding.enabled}
										<kbd class="px-2 py-1 text-xs font-mono rounded bg-surface-3 text-fg-muted border border-line-strong">
											{formatKeyCombo(binding.modifiers, binding.key)}
										</kbd>
									{:else if !binding.enabled}
										<span class="text-xs text-fg-disabled italic">disabled</span>
									{:else}
										<span class="text-xs text-fg-disabled">unset</span>
									{/if}
								</div>

								<!-- Actions -->
								<div class="flex items-center gap-1 flex-shrink-0">
									<!-- Enable/disable toggle -->
									<button
										type="button"
										on:click={() => toggleEnabled(binding)}
										class="p-1 rounded transition-colors {binding.enabled
											? 'text-success hover:text-success/80 hover:bg-surface-3/50'
											: 'text-fg-disabled hover:text-fg-muted hover:bg-surface-3/50'}"
										title={binding.enabled ? 'Disable shortcut' : 'Enable shortcut'}
									>
										{#if binding.enabled}
											<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
											</svg>
										{:else}
											<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
											</svg>
										{/if}
									</button>

									<!-- Edit button -->
									<button
										type="button"
										on:click={() => startRecording(binding.actionId)}
										class="p-1 rounded text-fg-subtle hover:text-fg-muted hover:bg-surface-3/50 transition-colors"
										title="Edit shortcut"
										disabled={recordingActionId !== null}
									>
										<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
										</svg>
									</button>

									<!-- Reset button (only if custom) -->
									{#if binding.isCustom}
										<button
											type="button"
											on:click={() => resetBinding(binding.actionId)}
											class="p-1 rounded text-fg-subtle hover:text-fg-muted hover:bg-surface-3/50 transition-colors"
											title="Reset to default"
										>
											<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
											</svg>
										</button>
									{/if}
								</div>
							</div>
						{/each}
					{/each}
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="px-5 py-3 bg-surface-2/50 flex items-center justify-between">
			<div class="flex items-center gap-3 text-xs text-fg-subtle">
				<span><kbd class="px-1.5 py-0.5 bg-surface-1 border border-line-strong rounded text-fg-muted text-[10px]">esc</kbd> close</span>
				{#if recordingActionId}
					<span class="text-signal">Recording... press Esc to cancel</span>
				{/if}
			</div>
			<button
				type="button"
				on:click={resetAll}
				class="text-sm text-danger hover:text-danger/80 transition-colors"
			>
				Reset All to Defaults
			</button>
		</div>
	</svelte:fragment>
</BaseModal>
