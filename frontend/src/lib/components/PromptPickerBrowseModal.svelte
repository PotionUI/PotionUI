<script module lang="ts">
	export interface PromptPickerTreeItem {
		id: string;
		name: string;
		description?: string;
	}
</script>

<script lang="ts">
	import BaseModal from './modals/BaseModal.svelte';
	import Icon from './Icon.svelte';
	import Button from './ui/Button.svelte';
	import type { AutocompleteValue, AutocompleteCurrentValue } from './AutocompleteDropdown.svelte';

	const TRIGGER_COLOR: Record<string, string> = {
		'#': 'rgb(var(--signal))',
		$: 'rgb(var(--ai-1))',
		'@': 'rgb(var(--success))',
		'/': 'rgb(var(--warning))'
	};

	let {
		triggerChar,
		title,
		initialQuery = '',
		contextBefore = '',
		contextMarker,
		contextAfter = '',
		mode,
		categories = [],
		values = [],
		getImageUrl,
		insertHint = 'Insert',
		currentValue = null,
		initialSelectedId = null,
		onSelectCategory,
		onInsertValue,
		onClose
	}: {
		triggerChar: '#' | '$' | '@' | '/';
		title: string;
		initialQuery?: string;
		contextBefore?: string;
		contextMarker: string;
		contextAfter?: string;
		mode: 'tree' | 'grid' | 'list';
		categories?: PromptPickerTreeItem[];
		values?: AutocompleteValue[];
		getImageUrl?: (fileId: string) => string;
		insertHint?: string;
		currentValue?: AutocompleteCurrentValue | null;
		initialSelectedId?: string | null;
		onSelectCategory?: (category: PromptPickerTreeItem) => void;
		onInsertValue: (value: AutocompleteValue) => void;
		onClose: () => void;
	} = $props();

	let query = $state(initialQuery);
	let selectedId = $state<string | null>(initialSelectedId);

	let filteredCategories = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return categories;
		return categories.filter(
			(c) => c.name.toLowerCase().includes(q) || (c.description ?? '').toLowerCase().includes(q)
		);
	});

	let filteredValues = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return values;
		return values.filter(
			(v) =>
				v.label.toLowerCase().includes(q) ||
				v.value.toLowerCase().includes(q) ||
				(v.description ?? '').toLowerCase().includes(q)
		);
	});

	$effect(() => {
		if (filteredValues.some((v) => v.id === selectedId)) return;
		if (initialSelectedId && filteredValues.some((v) => v.id === initialSelectedId)) {
			selectedId = initialSelectedId;
			return;
		}
		selectedId = filteredValues[0]?.id ?? null;
	});

	function rowLabel(value: AutocompleteValue): string {
		return triggerChar === '@' ? `<${value.label}>` : value.label;
	}

	function commitSelection() {
		const picked = filteredValues.find((v) => v.id === selectedId);
		if (!picked) return;
		onInsertValue(picked);
		onClose();
	}

	function pickValue(value: AutocompleteValue) {
		onInsertValue(value);
		onClose();
	}

	function handleClose() {
		onClose();
	}
</script>

<BaseModal
	isOpen={true}
	sizeClass="w-full md:w-[48rem] md:max-w-[calc(100vw-3rem)]"
	on:close={handleClose}
>
	<svelte:fragment slot="headerIcon">
		<span class="picker-chip" style="--trig: {TRIGGER_COLOR[triggerChar]}">{triggerChar}</span>
	</svelte:fragment>
	<svelte:fragment slot="header">
		<strong class="picker-modal-title">{title}</strong>
		<div class="picker-modal-search">
			<Icon name="search" className="icon" />
			<input bind:value={query} aria-label="Search {title}" />
		</div>
	</svelte:fragment>

	<div class="picker-context">
		{#if contextBefore}…{contextBefore}&nbsp;{/if}<span class="k" style="--trig: {TRIGGER_COLOR[triggerChar]}">{contextMarker}<span class="care"></span></span>&nbsp;{contextAfter}{#if contextAfter}…{/if}
	</div>

	{#if currentValue}
		<div class="pm-current">
			<div class="pm-current-thumb" style="--trig: {TRIGGER_COLOR[triggerChar]}">
				{#if currentValue.imageUrl}
					<img src={currentValue.imageUrl} alt={currentValue.label} />
				{:else}
					{triggerChar}
				{/if}
			</div>
			<div class="pm-current-copy">
				<span>Current value</span>
				<strong>{currentValue.label}</strong>
				{#if currentValue.value && currentValue.value !== currentValue.label}<p>{currentValue.value}</p>{/if}
				{#if currentValue.categoryLabel}<span class="pm-current-path">{currentValue.categoryLabel}</span>{/if}
			</div>
		</div>
	{/if}

	<div class="picker-modal-body">
		{#if mode === 'tree'}
			<div class="picker-tree">
				{#each filteredCategories as category (category.id)}
					<button type="button" class="picker-tree-row" onclick={() => onSelectCategory?.(category)}>
						<Icon name="folder" className="icon" />
						{category.name}
					</button>
				{/each}
			</div>
			<div class="picker-values">
				{#each filteredValues as value (value.id)}
					<button
						type="button"
						class="picker-vrow"
						class:sel={value.id === selectedId}
						onclick={() => (selectedId = value.id)}
						ondblclick={() => pickValue(value)}
					>
						<span class="picker-vthumb">
							{#if value.preview_file_id && getImageUrl}
								<img src={getImageUrl(value.preview_file_id)} alt={value.label} loading="lazy" />
							{:else}
								{triggerChar}
							{/if}
						</span>
						<span class="picker-vcopy">
							<strong>{rowLabel(value)}</strong>
							{#if value.description || value.value !== value.label}<span>{value.description ?? value.value}</span>{/if}
						</span>
						<span class="picker-vcheck"><Icon name="check" className="icon" /></span>
					</button>
				{/each}
			</div>
		{:else if mode === 'grid'}
			{#if filteredCategories.length > 0}
				<div class="picker-values full">
					{#each filteredCategories as category (category.id)}
						<button type="button" class="picker-vrow" onclick={() => onSelectCategory?.(category)}>
							<span class="picker-vthumb"><Icon name="folder" className="icon" /></span>
							<span class="picker-vcopy">
								<strong>{category.name}</strong>
								{#if category.description}<span>{category.description}</span>{/if}
							</span>
						</button>
					{/each}
				</div>
			{:else}
				<div class="picker-grid">
					{#each filteredValues as value (value.id)}
						<button
							type="button"
							class="picker-gitem"
							class:sel={value.id === selectedId}
							onclick={() => (selectedId = value.id)}
							ondblclick={() => pickValue(value)}
						>
							<span class="picker-gthumb">
								{#if value.preview_file_id && getImageUrl}
									<img src={getImageUrl(value.preview_file_id)} alt={value.label} loading="lazy" />
								{:else}
									<Icon name={value.kind === 'video' ? 'play' : 'image'} className="icon" />
								{/if}
							</span>
							<span class="picker-gcheck"><Icon name="check" className="icon" /></span>
							<span class="picker-gcopy">
								<strong>{rowLabel(value)}</strong>
								{#if value.value !== value.label}<span>{value.value}</span>{/if}
							</span>
						</button>
					{/each}
				</div>
			{/if}
		{:else}
			<div class="picker-values full">
				{#each filteredValues as value (value.id)}
					<button
						type="button"
						class="picker-vrow"
						class:sel={value.id === selectedId}
						onclick={() => (selectedId = value.id)}
						ondblclick={() => pickValue(value)}
					>
						<span class="picker-vthumb">
							<Icon name={triggerChar === '/' ? 'code' : 'braces'} className="icon" />
						</span>
						<span class="picker-vcopy">
							<strong>{value.label}</strong>
							{#if value.value}<span>{value.value}</span>{/if}
						</span>
						<span class="picker-vcheck"><Icon name="check" className="icon" /></span>
					</button>
				{/each}
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="picker-modal-foot">
			<Button variant="ghost" onclick={handleClose}>Cancel</Button>
			<Button variant="primary" disabled={!selectedId} onclick={commitSelection}>{insertHint}</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<style>
	.picker-chip {
		width: 26px;
		height: 26px;
		display: grid;
		place-items: center;
		flex: 0 0 auto;
		color: var(--trig);
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 5px;
		font: 600 13px 'IBM Plex Mono', monospace;
	}

	.picker-modal-title {
		font-size: 14px;
		font-weight: 600;
		white-space: nowrap;
	}

	.picker-modal-search {
		display: flex;
		align-items: center;
		gap: 7px;
		height: 32px;
		margin-left: auto;
		padding: 0 10px;
		width: 13rem;
		color: rgb(var(--fg));
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line-strong));
		border-radius: 5px;
		font-size: 13px;
	}

	.picker-modal-search :global(.icon) {
		width: 13px;
		height: 13px;
		color: rgb(var(--fg-subtle));
		flex: none;
	}

	.picker-modal-search input {
		width: 100%;
		color: inherit;
		background: transparent;
		border: 0;
		font: inherit;
	}

	.picker-modal-search input:focus {
		outline: 0;
	}

	.picker-context {
		display: flex;
		align-items: center;
		gap: 4px;
		min-height: 38px;
		padding: 0 14px;
		background: rgb(var(--surface-2) / 0.5);
		border-bottom: 1px solid rgb(var(--line));
		font-size: 13px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	.picker-context .k {
		color: var(--trig);
		font-weight: 600;
	}

	.picker-context .care {
		display: inline-block;
		width: 2px;
		height: 1em;
		background: var(--trig);
		vertical-align: -2px;
		margin: 0 1px;
	}

	.pm-current {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		margin: 10px 14px 0;
		padding: 9px;
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
	}

	.pm-current-thumb {
		width: 40px;
		height: 40px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		overflow: hidden;
		color: var(--trig);
		background: rgb(var(--surface-3));
		border: 1px solid rgb(var(--line));
		border-radius: 5px;
		font: 600 14px 'IBM Plex Mono', monospace;
	}

	.pm-current-thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.pm-current-copy {
		min-width: 0;
		flex: 1;
	}

	.pm-current-copy > span:first-child {
		display: block;
		margin-bottom: 4px;
		color: rgb(var(--fg-subtle));
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.07em;
	}

	.pm-current-copy strong {
		display: block;
		font-size: 14px;
		font-weight: 600;
		font-family: 'IBM Plex Mono', monospace;
		overflow-wrap: anywhere;
	}

	.pm-current-copy p {
		margin: 4px 0 0;
		color: rgb(var(--fg-muted));
		font-size: 12px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.pm-current-path {
		display: block;
		margin-top: 4px;
		color: rgb(var(--fg-subtle));
		font-size: 12px;
	}

	.picker-modal-body {
		flex: 1;
		min-height: 22rem;
		display: flex;
	}

	.picker-tree {
		width: 13rem;
		flex: 0 0 auto;
		padding: 8px;
		border-right: 1px solid rgb(var(--line));
		overflow-y: auto;
	}

	.picker-tree-row {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		height: 30px;
		padding: 0 8px;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		font-size: 13px;
		text-align: left;
	}

	.picker-tree-row:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}

	.picker-tree-row :global(.icon) {
		width: 13px;
		height: 13px;
		color: rgb(var(--fg-subtle));
		flex: none;
	}

	.picker-values {
		flex: 1;
		min-width: 0;
		padding: 8px;
		overflow-y: auto;
	}

	.picker-values.full {
		width: 100%;
	}

	.picker-vrow {
		display: flex;
		align-items: center;
		gap: 11px;
		width: 100%;
		min-height: 54px;
		padding: 7px 8px;
		border-radius: 6px;
		color: rgb(var(--fg-muted));
		text-align: left;
	}

	.picker-vrow:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}

	.picker-vrow.sel {
		background: rgb(var(--signal) / 0.1);
		color: rgb(var(--fg));
		box-shadow: inset 0 0 0 1px rgb(var(--signal) / 0.35);
	}

	.picker-vthumb {
		width: 40px;
		height: 40px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		overflow: hidden;
		color: var(--trig);
		background: rgb(var(--surface-2));
		border: 1px solid rgb(var(--line));
		border-radius: 5px;
		font: 600 14px 'IBM Plex Mono', monospace;
	}

	.picker-vthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.picker-vthumb :global(.icon) {
		width: 15px;
		height: 15px;
	}

	.picker-vcopy {
		min-width: 0;
		flex: 1;
	}

	.picker-vcopy strong {
		display: block;
		font-size: 14px;
		font-weight: 600;
		font-family: 'IBM Plex Mono', monospace;
	}

	.picker-vcopy span {
		display: block;
		margin-top: 3px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.picker-vcheck {
		width: 18px;
		height: 18px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		color: transparent;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 50%;
	}

	.picker-vrow.sel .picker-vcheck {
		color: rgb(var(--accent-contrast));
		background: rgb(var(--signal));
		border-color: rgb(var(--signal));
	}

	.picker-vcheck :global(.icon) {
		width: 10px;
		height: 10px;
	}

	.picker-grid {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 10px;
		padding: 12px;
		width: 100%;
	}

	.picker-gitem {
		position: relative;
		display: flex;
		flex-direction: column;
		border-radius: 6px;
		overflow: hidden;
		border: 1px solid rgb(var(--line));
		background: rgb(var(--surface-2));
		text-align: left;
	}

	.picker-gitem:hover {
		border-color: rgb(var(--line-strong));
	}

	.picker-gitem.sel {
		box-shadow: 0 0 0 2px rgb(var(--signal));
		border-color: transparent;
	}

	.picker-gthumb {
		height: 72px;
		display: grid;
		place-items: center;
		background: linear-gradient(
			135deg,
			rgb(var(--surface-3)) 0%,
			rgb(var(--surface-2)) 55%,
			rgb(var(--surface-3)) 100%
		);
		overflow: hidden;
	}

	.picker-gthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.picker-gthumb :global(.icon) {
		width: 16px;
		height: 16px;
		color: rgb(var(--fg-muted));
	}

	.picker-gcheck {
		position: absolute;
		top: 6px;
		right: 6px;
		width: 18px;
		height: 18px;
		display: grid;
		place-items: center;
		color: transparent;
		border: 1px solid rgb(var(--line-hover));
		border-radius: 50%;
		background: rgb(var(--surface-1) / 0.7);
	}

	.picker-gitem.sel .picker-gcheck {
		color: rgb(var(--accent-contrast));
		background: rgb(var(--signal));
		border-color: rgb(var(--signal));
	}

	.picker-gcheck :global(.icon) {
		width: 10px;
		height: 10px;
	}

	.picker-gcopy {
		padding: 7px 8px 9px;
	}

	.picker-gcopy strong {
		display: block;
		font-size: 13px;
		font-weight: 600;
		font-family: 'IBM Plex Mono', monospace;
	}

	.picker-gcopy span {
		display: block;
		margin-top: 2px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.picker-modal-foot {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 8px;
		min-height: 52px;
		padding: 0 14px;
	}
</style>
