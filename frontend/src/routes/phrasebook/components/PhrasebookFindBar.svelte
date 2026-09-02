<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Kbd, Spinner } from '$lib/components/ui';
	import type { PhrasebookFindMode, PhrasebookFindScope } from '$lib/types/api';
	import { isSearching, type FindFilters } from '../phrasebookSearch';

	let {
		filters,
		searching = false,
		error = null,
		topLevel = [],
		onChange,
		onClear
	}: {
		filters: FindFilters;
		searching?: boolean;
		error?: string | null;
		topLevel?: { id: string; name: string; path: string }[];
		onChange: (patch: Partial<FindFilters>) => void;
		onClear: () => void;
	} = $props();

	const modes: { id: PhrasebookFindMode; label: string }[] = [
		{ id: 'contains', label: 'Contains' },
		{ id: 'word', label: 'Word' },
		{ id: 'regex', label: 'Regex' }
	];

	const scopes: { id: PhrasebookFindScope; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'values', label: 'Values' },
		{ id: 'categories', label: 'Categories' }
	];

	let inputEl: HTMLInputElement | undefined = $state();
	let active = $derived(isSearching(filters.query));

	function handleWindowKeydown(e: KeyboardEvent) {
		if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
		const target = e.target as HTMLElement | null;
		const tag = target?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) return;
		e.preventDefault();
		inputEl?.focus();
		inputEl?.select();
	}

	function handleInputKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			onClear();
		}
	}

	const toggleClass = (pressed: boolean) =>
		`px-2 py-1 text-xs rounded-sm transition-colors duration-100 ${
			pressed ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'
		}`;
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="bg-surface-1 border-b border-line px-4 py-2 flex-shrink-0" data-find-bar>
	<div class="flex items-center gap-3 flex-wrap">
		<div class="relative flex-1 min-w-[16rem]">
			<Icon
				name="search"
				className="w-4 h-4 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
			/>
			<input
				bind:this={inputEl}
				type="text"
				class="input text-sm py-1.5 pl-8 pr-16 bg-surface-2/50 w-full {error ? 'border-danger focus:ring-danger' : ''}"
				placeholder="Find in phrasebook…"
				aria-label="Find in phrasebook"
				aria-invalid={!!error}
				data-find-input
				value={filters.query}
				oninput={(e) => onChange({ query: e.currentTarget.value })}
				onkeydown={handleInputKeydown}
			/>
			<div class="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
				{#if searching}
					<Spinner size="sm" />
				{:else if !active}
					<Kbd keys="/" />
				{:else}
					<Tooltip text="Clear" kbd="Esc" position="bottom">
						<button
							type="button"
							class="p-0.5 rounded text-fg-muted hover:text-fg hover:bg-surface-3/50 transition-colors"
							aria-label="Clear search"
							onclick={onClear}
						>
							<Icon name="close" className="w-3.5 h-3.5" />
						</button>
					</Tooltip>
				{/if}
			</div>
		</div>

		<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5 flex-shrink-0" role="group" aria-label="Match mode">
			{#each modes as mode (mode.id)}
				<button
					type="button"
					class={toggleClass(filters.mode === mode.id)}
					aria-pressed={filters.mode === mode.id}
					onclick={() => onChange({ mode: mode.id })}
				>
					{mode.label}
				</button>
			{/each}
		</div>

		<Tooltip text="Match case" position="bottom">
			<button
				type="button"
				class="font-mono {toggleClass(filters.caseSensitive)} bg-surface-2/50"
				aria-pressed={filters.caseSensitive}
				aria-label="Match case"
				onclick={() => onChange({ caseSensitive: !filters.caseSensitive })}
			>
				Aa
			</button>
		</Tooltip>

		{#if filters.scope !== 'categories'}
			<div class="flex items-center gap-2 text-xs text-fg-muted flex-shrink-0" role="group" aria-label="Value fields">
				<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">in</span>
				<label class="flex items-center gap-1.5 cursor-pointer select-none">
					<input
						type="checkbox"
						class="accent-accent"
						checked={filters.inLabel}
						disabled={filters.inLabel && !filters.inValue}
						onchange={(e) => onChange({ inLabel: e.currentTarget.checked })}
					/>
					Label
				</label>
				<label class="flex items-center gap-1.5 cursor-pointer select-none">
					<input
						type="checkbox"
						class="accent-accent"
						checked={filters.inValue}
						disabled={filters.inValue && !filters.inLabel}
						onchange={(e) => onChange({ inValue: e.currentTarget.checked })}
					/>
					Value
				</label>
			</div>
		{/if}

		<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5 flex-shrink-0" role="group" aria-label="Scope">
			{#each scopes as scope (scope.id)}
				<button
					type="button"
					class={toggleClass(filters.scope === scope.id)}
					aria-pressed={filters.scope === scope.id}
					onclick={() => onChange({ scope: scope.id })}
				>
					{scope.label}
				</button>
			{/each}
		</div>

		<label class="flex items-center gap-1.5 text-xs text-fg-muted cursor-pointer select-none flex-shrink-0">
			<input
				type="checkbox"
				class="accent-accent"
				checked={filters.includeInactive}
				onchange={(e) => onChange({ includeInactive: e.currentTarget.checked })}
			/>
			Include inactive
		</label>

		<select
			class="input text-xs py-1 min-h-0 h-7 w-auto max-w-[12rem] bg-surface-2/50"
			aria-label="Search within"
			value={filters.pathPrefix}
			onchange={(e) => onChange({ pathPrefix: e.currentTarget.value })}
		>
			<option value="">Everywhere</option>
			{#each topLevel as category (category.id)}
				<option value={category.path}>{category.name}</option>
			{/each}
		</select>

		{#if active}
			<Button variant="ghost" size="sm" icon="arrow-left" onclick={onClear}>Back to browsing</Button>
		{/if}
	</div>

	{#if error}
		<p class="mt-1.5 pl-8 text-xs text-danger" data-find-error role="alert">{error}</p>
	{/if}
</div>
