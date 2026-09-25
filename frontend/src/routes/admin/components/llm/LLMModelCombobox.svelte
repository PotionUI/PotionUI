<script lang="ts">
	import { onDestroy } from 'svelte';
	import { api } from '$lib/services/api/index';
	import type { DiscoveredLLMModel } from '$lib/types/llm';
	import { IconButton, Spinner } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import { filterModels, modelMeta, splitMatch } from './llmModelSearch';

	let {
		value = $bindable(''),
		id,
		type,
		baseUrl,
		apiKey = '',
		configId = null,
		placeholder = 'Pick or type a model',
		debounceMs = 400
	}: {
		value: string;
		id: string;
		type: string;
		baseUrl: string;
		apiKey?: string;
		configId?: string | null;
		placeholder?: string;
		debounceMs?: number;
	} = $props();

	const listboxId = $derived(`${id}-listbox`);

	let models = $state<DiscoveredLLMModel[]>([]);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let loaded = $state(false);
	let open = $state(false);
	let query = $state('');
	let activeIndex = $state(0);
	let position = $state<FlippedMenuPosition | null>(null);
	let listWidth = $state(384);
	let wrapper = $state<HTMLDivElement | null>(null);
	let listbox = $state<HTMLDivElement | null>(null);
	let requestSeq = 0;
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;

	const filtered = $derived(filterModels(models, query));
	const customValue = $derived(query.trim());
	const showCustomRow = $derived(loaded && !loading && filtered.length === 0 && customValue.length > 0);

	async function refresh() {
		if (debounceTimer) {
			clearTimeout(debounceTimer);
			debounceTimer = null;
		}
		const seq = ++requestSeq;
		if (!baseUrl.trim()) {
			models = [];
			error = null;
			loading = false;
			loaded = false;
			return;
		}
		loading = true;
		error = null;
		try {
			const response = await api.discoverLLMModels({
				type,
				base_url: baseUrl.trim(),
				api_key: apiKey || null,
				config_id: configId
			});
			if (seq !== requestSeq) return;
			if (response.success && response.data) {
				models = response.data.models ?? [];
				error = response.data.error ?? null;
			} else {
				models = [];
				error = response.message || 'Failed to list models.';
			}
		} catch {
			if (seq !== requestSeq) return;
			models = [];
			error = 'Failed to reach the server to list models.';
		} finally {
			if (seq === requestSeq) {
				loading = false;
				loaded = true;
			}
		}
	}

	$effect(() => {
		void [type, baseUrl, apiKey, configId];
		if (debounceTimer) clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			debounceTimer = null;
			refresh();
		}, debounceMs);
		return () => {
			if (debounceTimer) clearTimeout(debounceTimer);
		};
	});

	onDestroy(() => {
		requestSeq += 1;
	});

	function reposition() {
		if (!wrapper) return;
		listWidth = Math.max(wrapper.getBoundingClientRect().width, 384);
		position = computeFlippedMenuPosition(wrapper, { width: listWidth, heightEstimate: 320, gap: 4 });
	}

	function openList() {
		if (open) return;
		query = '';
		activeIndex = 0;
		reposition();
		open = true;
	}

	function closeList() {
		open = false;
	}

	function pick(modelId: string) {
		value = modelId;
		query = '';
		closeList();
	}

	function handleInput(event: Event) {
		value = (event.currentTarget as HTMLInputElement).value;
		query = value;
		activeIndex = 0;
		if (!open) {
			reposition();
			open = true;
		}
	}

	function scrollActiveIntoView() {
		queueMicrotask(() => {
			listbox?.querySelector<HTMLElement>(`#${CSS.escape(`${id}-option-${activeIndex}`)}`)?.scrollIntoView?.({ block: 'nearest' });
		});
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			if (!open) {
				openList();
				return;
			}
			if (filtered.length > 0) activeIndex = (activeIndex + 1) % filtered.length;
			scrollActiveIntoView();
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			if (!open) {
				openList();
				return;
			}
			if (filtered.length > 0) activeIndex = (activeIndex - 1 + filtered.length) % filtered.length;
			scrollActiveIntoView();
		} else if (event.key === 'Enter') {
			if (!open) return;
			event.preventDefault();
			const choice = filtered[activeIndex];
			if (choice) pick(choice.id);
			else if (customValue) pick(customValue);
			else closeList();
		} else if (event.key === 'Escape') {
			if (!open) return;
			event.preventDefault();
			event.stopPropagation();
			closeList();
		} else if (event.key === 'Tab') {
			closeList();
		}
	}

	function handleWindowPointerDown(event: PointerEvent) {
		if (!open) return;
		const target = event.target as Node | null;
		if (target && (wrapper?.contains(target) || listbox?.contains(target))) return;
		closeList();
	}

	function handleViewportChange() {
		if (open) reposition();
	}

	const verticalStyle = $derived(
		position ? (position.top !== undefined ? `top: ${position.top}px;` : `bottom: ${position.bottom}px;`) : ''
	);
</script>

<svelte:window
	onpointerdown={handleWindowPointerDown}
	onresize={handleViewportChange}
	onscrollcapture={handleViewportChange}
/>

<div bind:this={wrapper} class="flex items-center gap-1.5" data-testid="llm-model-combobox">
	<input
		{id}
		type="text"
		class="input flex-1 min-w-0"
		role="combobox"
		autocomplete="off"
		spellcheck="false"
		aria-autocomplete="list"
		aria-expanded={open}
		aria-controls={listboxId}
		aria-activedescendant={open && filtered[activeIndex] ? `${id}-option-${activeIndex}` : undefined}
		{placeholder}
		{value}
		oninput={handleInput}
		onfocus={openList}
		onclick={openList}
		onkeydown={handleKeydown}
	/>
	<Tooltip text="Refresh models">
		<IconButton icon="refresh" label="Refresh models" size="sm" variant="secondary" disabled={loading} onclick={refresh} />
	</Tooltip>
</div>

{#if loading}
	<p class="mt-1 flex items-center gap-1.5 text-xs text-fg-subtle" data-testid="llm-model-loading">
		<Spinner size="sm" /> Loading models…
	</p>
{:else if error}
	<p class="mt-1 text-xs text-danger" role="alert" data-testid="llm-model-error">{error}</p>
{:else if loaded && models.length > 0}
	<p class="mt-1 text-xs text-fg-subtle">
		<span class="font-mono tabular-nums">{models.length}</span> {models.length === 1 ? 'model' : 'models'} available
	</p>
{/if}

{#if open && position}
	<div use:portal style="display: contents;">
		<div
			bind:this={listbox}
			id={listboxId}
			role="listbox"
			aria-label="Available models"
			class="fixed z-[99999] overflow-y-auto rounded-xl border border-line bg-surface-1 p-1 shadow-floating"
			style="left: {position.left}px; {verticalStyle} width: min({listWidth}px, calc(100vw - 2rem)); max-height: {Math.min(position.maxHeight, 320)}px;"
		>
			{#if loading && models.length === 0}
				<div class="flex items-center gap-2 px-3 py-2 text-sm text-fg-subtle">
					<Spinner size="sm" /> Loading models…
				</div>
			{:else if error && models.length === 0}
				<div class="px-3 py-2 text-sm text-fg-subtle">No model list — type the model name.</div>
			{:else if !loaded && models.length === 0}
				<div class="px-3 py-2 text-sm text-fg-subtle">Set the base URL to list models.</div>
			{:else}
				{#each filtered as model, index (model.id)}
					{@const meta = modelMeta(model)}
					<div
						id="{id}-option-{index}"
						role="option"
						tabindex="-1"
						aria-selected={index === activeIndex}
						class="flex cursor-pointer items-center justify-between gap-3 rounded px-3 py-1.5 text-sm {index === activeIndex ? 'bg-signal/10 text-fg' : 'text-fg-muted hover:bg-surface-2'}"
						onpointerdown={(event) => event.preventDefault()}
						onpointerenter={() => (activeIndex = index)}
						onclick={() => pick(model.id)}
						onkeydown={(event) => event.key === 'Enter' && pick(model.id)}
					>
						<span class="min-w-0 truncate font-mono">
							{#each splitMatch(model.id, query) as part}
								{#if part.match}<mark class="rounded-sm bg-signal/20 text-fg">{part.text}</mark>{:else}{part.text}{/if}
							{/each}
						</span>
						{#if meta.length > 0}
							<span class="shrink-0 font-mono text-xs tabular-nums text-fg-subtle">{meta.join(' · ')}</span>
						{/if}
					</div>
				{/each}
				{#if showCustomRow}
					<div
						role="option"
						tabindex="-1"
						aria-selected="true"
						class="cursor-pointer rounded px-3 py-1.5 text-sm hover:bg-surface-2"
						data-testid="llm-model-custom"
						onpointerdown={(event) => event.preventDefault()}
						onclick={() => pick(customValue)}
						onkeydown={(event) => event.key === 'Enter' && pick(customValue)}
					>
						<span class="block text-fg-subtle">No models match</span>
						<span class="block text-xs text-fg-muted">Use <span class="font-mono text-fg">{customValue}</span> as a custom model</span>
					</div>
				{:else if filtered.length === 0}
					<div class="px-3 py-2 text-sm text-fg-subtle">No models found</div>
				{/if}
			{/if}
		</div>
	</div>
{/if}
