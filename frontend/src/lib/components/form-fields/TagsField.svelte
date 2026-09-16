<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import TagsPicker from './TagsPicker.svelte';
	import {
		addTag,
		canAddMore as canAddMoreTo,
		clearCategory,
		joinTagsValue,
		normalizeTagsValue,
		removeLastTag,
		removeTag,
		toggleTag,
		totalTagCount,
		type TagsCategory,
		type TagsMap
	} from './tagsValue';

	let {
		name,
		config = {},
		value,
		onChange
	}: {
		name: string | null;
		config?: any;
		value?: unknown;
		onChange: (fieldName: string, value: unknown) => void;
	} = $props();

	let label = $derived(config.title || name || '');
	let description = $derived(config.description || '');
	let tooltip = $derived(config.tooltip);
	let disabled = $derived(!!config.disabled);
	let required = $derived(!!config.required);
	let categories = $derived((config.categories ?? []) as TagsCategory[]);
	let separator = $derived(typeof config.separator === 'string' ? config.separator : ', ');
	let maxTags = $derived(typeof config.max_tags === 'number' ? config.max_tags : undefined);
	let fieldAllowCustom = $derived(config.allow_custom !== false);

	let currentValue = $derived(normalizeTagsValue(value, categories, separator));
	let previewText = $derived(joinTagsValue(currentValue, categories, separator));
	let totalCount = $derived(totalTagCount(currentValue));
	let emptyPreviewText = $derived(
		required ? 'Nothing yet — required before generating' : 'Nothing yet'
	);
	let displayedPreviewText = $derived(totalCount === 0 ? emptyPreviewText : previewText);

	let rootEl: HTMLDivElement | undefined = $state();
	let openKey = $state<string | null>(null);
	let popoverPos = $state<FlippedMenuPosition | null>(null);

	function emit(next: TagsMap) {
		if (!name || next === currentValue) return;
		onChange(name, next);
	}

	function reposition() {
		if (!openKey || !rootEl) return;
		const escaped = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(openKey) : openKey;
		const trigger = rootEl.querySelector(`[data-slot-key="${escaped}"]`) as HTMLElement | null;
		if (!trigger) return;
		popoverPos = computeFlippedMenuPosition(trigger, { width: 384, heightEstimate: 420, gap: 6 });
	}

	async function openPicker(key: string) {
		if (disabled) return;
		if (openKey === key) {
			openKey = null;
			return;
		}
		openKey = key;
		await tick();
		reposition();
	}

	function closePicker() {
		openKey = null;
	}

	function slotCanAddMore(cat: TagsCategory): boolean {
		return canAddMoreTo(currentValue, cat, maxTags);
	}

	function handleToggle(key: string, tag: string) {
		emit(toggleTag(currentValue, key, tag, categories, maxTags));
	}

	function handleAddCustom(key: string, tag: string) {
		emit(addTag(currentValue, key, tag, categories, maxTags));
	}

	function handleRemoveChip(key: string, tag: string) {
		emit(removeTag(currentValue, key, tag));
	}

	function handleClearSlot(key: string) {
		emit(clearCategory(currentValue, key));
	}

	function handleBackspaceEmpty(key: string) {
		emit(removeLastTag(currentValue, key));
	}
</script>

<div class="field-card" bind:this={rootEl}>
	<div class="mb-1.5 flex items-center gap-1">
		<span class="label">
			{label}
			{#if required}<span class="ml-0.5 text-danger">*</span>{/if}
		</span>
		{#if tooltip}
			<Tooltip text={tooltip} position="top">
				<span class="inline-flex cursor-help items-center text-fg-subtle">
					<Icon name="info" className="w-3.5 h-3.5" />
				</span>
			</Tooltip>
		{/if}
	</div>
	{#if description}
		<p class="mb-2 text-xs text-fg-muted">{description}</p>
	{/if}

	<div class="flex flex-wrap items-start gap-1.5" class:opacity-60={disabled}>
		{#each categories as cat (cat.key)}
			{@const chips = currentValue[cat.key] ?? []}
			{#if !cat.multi}
				{@const chosen = chips[0]}
				<div
					data-slot-key={cat.key}
					class="tags-slot-box relative inline-flex h-[34px] w-full items-center gap-1.5 rounded border pl-2.5 pr-1.5 sm:w-auto
						{chosen ? 'border-line-strong bg-surface-3' : 'border-line bg-field-bg'}
						{openKey === cat.key ? 'ring-1 ring-line-hover' : ''}"
				>
					<button
						type="button"
						{disabled}
						onclick={() => openPicker(cat.key)}
						class="flex flex-1 items-center gap-1.5 text-left disabled:cursor-not-allowed"
					>
						<span class="whitespace-nowrap text-xs font-mono uppercase tracking-wide text-fg-subtle">{cat.label}</span>
						{#if chosen}
							<span class="whitespace-nowrap text-sm font-medium text-fg">{chosen}</span>
						{:else}
							<span class="text-sm text-fg-subtle">—</span>
						{/if}
						<Icon name="chevron-down" className="w-3.5 h-3.5 shrink-0 text-fg-subtle" />
					</button>
					{#if chosen && !disabled}
						<IconButton
							icon="close"
							label="Clear {cat.label}"
							size="sm"
							onclick={(e) => {
								e.stopPropagation();
								handleClearSlot(cat.key);
							}}
						/>
					{/if}
					{#if openKey === cat.key && popoverPos}
						<TagsPicker
							category={cat}
							selected={chips}
							position={popoverPos}
							{fieldAllowCustom}
							canAddMore={slotCanAddMore(cat)}
							onToggle={(tag) => handleToggle(cat.key, tag)}
							onAddCustom={(tag) => handleAddCustom(cat.key, tag)}
							onBackspaceEmpty={() => handleBackspaceEmpty(cat.key)}
							onClose={closePicker}
						/>
					{/if}
				</div>
			{:else}
				<div
					data-slot-key={cat.key}
					class="tags-slot-box relative inline-flex w-full flex-wrap items-center gap-1.5 rounded border px-2 py-1 min-h-[34px] sm:w-auto
						{chips.length > 0 ? 'border-line-strong bg-surface-3' : 'border-line bg-field-bg'}
						{openKey === cat.key ? 'ring-1 ring-line-hover' : ''}"
				>
					<span class="whitespace-nowrap text-xs font-mono uppercase tracking-wide text-fg-subtle">{cat.label}</span>
					{#each chips as tag (tag)}
						<span class="inline-flex items-center gap-1 rounded border border-line bg-surface-2 px-2 py-0.5 text-sm text-fg">
							{tag}
							{#if !disabled}
								<button
									type="button"
									aria-label="Remove {tag}"
									onclick={() => handleRemoveChip(cat.key, tag)}
									class="grid h-4 w-4 place-items-center rounded text-fg-subtle hover:text-danger"
								>
									<Icon name="close" className="w-2.5 h-2.5" />
								</button>
							{/if}
						</span>
					{/each}
					{#if !disabled}
						{#if chips.length > 0}
							<IconButton icon="plus" label="Add {cat.label}" size="sm" onclick={() => openPicker(cat.key)} />
						{:else}
							<button
								type="button"
								onclick={() => openPicker(cat.key)}
								class="inline-flex h-[22px] items-center gap-1 rounded border border-dashed border-line-strong px-2 text-sm text-fg-subtle hover:bg-surface-2 hover:text-fg"
							>
								Add
							</button>
						{/if}
					{/if}
					{#if openKey === cat.key && popoverPos}
						<TagsPicker
							category={cat}
							selected={chips}
							position={popoverPos}
							{fieldAllowCustom}
							canAddMore={slotCanAddMore(cat)}
							onToggle={(tag) => handleToggle(cat.key, tag)}
							onAddCustom={(tag) => handleAddCustom(cat.key, tag)}
							onBackspaceEmpty={() => handleBackspaceEmpty(cat.key)}
							onClose={closePicker}
						/>
					{/if}
				</div>
			{/if}
		{/each}
	</div>

	<div class="mt-2.5 flex items-center gap-2 rounded border border-line bg-field-bg px-2.5 py-2">
		<span class="shrink-0 rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-xs uppercase tracking-wide text-fg-subtle">
			Sends
		</span>
		<span class="flex-1 font-mono text-xs leading-relaxed text-fg-muted">{displayedPreviewText}</span>
		{#if maxTags != null}
			<span class="ml-auto shrink-0 font-mono text-xs tabular-nums text-fg-subtle">{totalCount} / {maxTags}</span>
		{/if}
	</div>
</div>
