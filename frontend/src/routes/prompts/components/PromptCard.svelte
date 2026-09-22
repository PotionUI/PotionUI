<script lang="ts">
	import type { Prompt } from '$lib/types/segments';
	import { Button, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { parseVariableUsageTokens } from '$lib/utils/promptVariables';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { promptCardTitle, summarizePromptVariables } from '$lib/prompts/promptCardDisplay';

	let {
		prompt,
		selected = false,
		onToggleSelect,
		onOpen,
		onUse,
		onCopy,
		onDuplicate,
		onAddToCollection,
		onExport,
		onDelete
	}: {
		prompt: Prompt;
		selected?: boolean;
		onToggleSelect: (prompt: Prompt) => void;
		onOpen: (prompt: Prompt) => void;
		onUse: (prompt: Prompt) => void;
		onCopy: (prompt: Prompt) => void;
		onDuplicate: (prompt: Prompt) => void;
		onAddToCollection: (prompt: Prompt) => void;
		onExport: (prompt: Prompt) => void;
		onDelete: (prompt: Prompt) => void;
	} = $props();

	let menuOpen = $state(false);
	let menuEl: HTMLDivElement | undefined = $state();

	const title = $derived(promptCardTitle(prompt));
	const negative = $derived(prompt.usage_hint === 'negative');
	const thumbnailUrl = $derived(prompt.cover_thumbnail || null);
	const textTokens = $derived(parseVariableUsageTokens(prompt.flattened_text || ''));
	const variablesSummary = $derived(summarizePromptVariables(prompt.variables));
	const sourceLabel = $derived(
		prompt.source_provider && prompt.source_provider !== 'manual' ? prompt.source_provider : null
	);
	const usageLabel = $derived(
		prompt.usage_count ? `used ${prompt.usage_count}×` : 'never used'
	);
	const relativeLabel = $derived.by(() => {
		const when = prompt.last_used_at || prompt.created_at;
		return when ? timeAgo(when) : null;
	});

	function closeMenu() {
		menuOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		if (menuOpen && menuEl && !menuEl.contains(event.target as Node)) closeMenu();
	}

	function handleCardClick() {
		onOpen(prompt);
	}

	function handleCardKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			onOpen(prompt);
		} else if (event.key === ' ') {
			event.preventDefault();
			onToggleSelect(prompt);
		}
	}

	function stop(fn: () => void) {
		return (event: Event) => {
			event.stopPropagation();
			fn();
		};
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div
	class="group relative flex min-w-0 gap-3 rounded-lg border p-3 shadow-raised transition-colors {selected
		? 'border-signal bg-signal/[0.06] shadow-[0_0_0_1px_rgb(var(--signal))]'
		: 'border-line-strong bg-surface-1 hover:border-line-hover'}"
	role="button"
	tabindex="0"
	data-prompt-card
	aria-label={title.text}
	onclick={handleCardClick}
	onkeydown={handleCardKeydown}
>
	<button
		type="button"
		class="mt-1 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-opacity {selected
			? 'border-accent bg-accent text-accent-contrast opacity-100'
			: 'border-line-hover bg-surface-2 text-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}"
		onclick={stop(() => onToggleSelect(prompt))}
		aria-label={selected ? 'Deselect prompt' : 'Select prompt'}
		aria-pressed={selected}
	>
		{#if selected}<Icon name="check" className="h-3 w-3" strokeWidth={3} />{/if}
	</button>

	{#if thumbnailUrl}
		<div class="relative h-24 w-24 flex-shrink-0 overflow-hidden rounded bg-surface-3">
			<img src={thumbnailUrl} alt="" class="h-full w-full object-cover" loading="lazy" decoding="async" />
			{#if prompt.generation_count}
				<span
					class="absolute bottom-1 left-1 rounded bg-black/55 px-1.5 py-0 font-mono text-xs text-white backdrop-blur-sm"
				>
					{prompt.generation_count} gens
				</span>
			{/if}
		</div>
	{/if}

	<div class="flex min-w-0 flex-1 flex-col gap-1.5">
		<div class="flex min-w-0 items-center gap-2">
			<Tooltip text={negative ? 'Negative prompt' : 'Positive prompt'}>
				<span
					class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border font-mono text-xs {negative
						? 'border-danger/35 text-danger'
						: 'border-line-strong bg-surface-2 text-fg-muted'}"
				>
					{negative ? '−' : '+'}
				</span>
			</Tooltip>
			<h3
				class="min-w-0 flex-1 truncate text-sm font-semibold {title.untitled
					? 'font-medium italic text-fg-muted'
					: 'text-fg'}"
			>
				{title.text}
			</h3>
			<div class="flex flex-shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
				<Button size="xs" variant="primary" onclick={stop(() => onUse(prompt))}>Use</Button>
				<Button size="xs" variant="secondary" onclick={stop(() => onOpen(prompt))}>Edit</Button>
				<div class="relative" bind:this={menuEl}>
					<Tooltip text="More actions">
						<IconButton
							icon="more"
							label="More actions"
							size="sm"
							ariaExpanded={menuOpen}
							onclick={stop(() => (menuOpen = !menuOpen))}
						/>
					</Tooltip>
					{#if menuOpen}
						<div
							class="absolute right-0 top-[calc(100%+4px)] z-30 min-w-[180px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
							role="menu"
						>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
								onclick={stop(() => {
									closeMenu();
									onCopy(prompt);
								})}
							>
								<Icon name="copy" className="h-3.5 w-3.5" />
								Copy prompt
							</button>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
								onclick={stop(() => {
									closeMenu();
									onDuplicate(prompt);
								})}
							>
								<Icon name="copy" className="h-3.5 w-3.5" />
								Duplicate
							</button>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
								onclick={stop(() => {
									closeMenu();
									onAddToCollection(prompt);
								})}
							>
								<Icon name="folder" className="h-3.5 w-3.5" />
								Add to collection
							</button>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
								onclick={stop(() => {
									closeMenu();
									onExport(prompt);
								})}
							>
								<Icon name="download" className="h-3.5 w-3.5" />
								Export
							</button>
							<div class="my-1 border-t border-line"></div>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-danger hover:bg-danger/10"
								onclick={stop(() => {
									closeMenu();
									onDelete(prompt);
								})}
							>
								<Icon name="trash" className="h-3.5 w-3.5" />
								Delete
							</button>
						</div>
					{/if}
				</div>
			</div>
		</div>

		<p class="line-clamp-4 text-sm leading-relaxed text-fg-muted">
			{#each textTokens as token, index (index)}
				{#if token.type === 'variable'}<span class="font-mono text-xs text-signal">{token.raw}</span
					>{:else}{token.raw}{/if}
			{/each}
		</p>

		<div class="mt-auto flex flex-wrap items-center gap-1.5">
			<span
				class="inline-flex h-5 max-w-full items-center gap-1 truncate rounded border border-line-strong bg-surface-2 px-1.5 font-mono text-xs text-fg-muted"
			>
				{prompt.model_name || 'any model'}
			</span>
			{#if sourceLabel}
				<span class="inline-flex h-5 items-center rounded border border-line-strong bg-surface-2 px-1.5 font-mono text-xs text-fg-muted">
					{sourceLabel}
				</span>
			{/if}
			{#if prompt.nsfw}
				<span class="inline-flex h-5 items-center rounded border border-line-strong bg-surface-2 px-1.5 font-mono text-xs text-fg-muted">
					nsfw
				</span>
			{/if}
			{#if variablesSummary.count > 0}
				<Tooltip text={variablesSummary.broken ? 'A condition references a missing variable' : 'Variables used in this prompt'}>
					<span
						class="inline-flex h-5 items-center gap-1 rounded border px-1.5 font-mono text-xs {variablesSummary.broken
							? 'border-warning/35 bg-warning/10 text-warning'
							: 'border-signal/28 bg-signal/10 text-signal'}"
					>
						<Icon name="braces" className="h-2.5 w-2.5" />
						{variablesSummary.count}{variablesSummary.linked > 0 ? ` · ${variablesSummary.linked} linked` : ''}
					</span>
				</Tooltip>
			{/if}
			<span class="ml-auto flex items-center gap-2.5 whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">
				<span>{usageLabel}</span>
				{#if relativeLabel}<span>{relativeLabel}</span>{/if}
			</span>
		</div>
	</div>
</div>
