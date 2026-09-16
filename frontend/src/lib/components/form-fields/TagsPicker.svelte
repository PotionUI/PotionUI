<script lang="ts">
	import { tick } from 'svelte';
	import portal from '$lib/actions/portal';
	import Icon from '$lib/components/Icon.svelte';
	import { Input, Kbd } from '$lib/components/ui';
	import type { FlippedMenuPosition } from '$lib/utils/menuPosition';
	import { canAddCustom, filterCategoryTags, hasExactMatch, type TagsCategory } from './tagsValue';

	type PickerOption = { kind: 'tag'; tag: string } | { kind: 'free'; text: string };

	let {
		category,
		selected,
		position,
		fieldAllowCustom,
		canAddMore,
		onToggle,
		onAddCustom,
		onBackspaceEmpty,
		onClose
	}: {
		category: TagsCategory;
		selected: string[];
		position: FlippedMenuPosition;
		fieldAllowCustom: boolean;
		canAddMore: boolean;
		onToggle: (tag: string) => void;
		onAddCustom: (tag: string) => void;
		onBackspaceEmpty: () => void;
		onClose: () => void;
	} = $props();

	let searchValue = $state('');
	let activeIndex = $state(0);
	let panelEl: HTMLDivElement | undefined = $state();

	let allowCustom = $derived(canAddCustom(fieldAllowCustom, category));
	let filteredTags = $derived(filterCategoryTags(category, searchValue));
	let showFreeRow = $derived(
		searchValue.trim().length > 0 && allowCustom && !hasExactMatch(category, searchValue)
	);
	let options = $derived<PickerOption[]>([
		...(showFreeRow ? [{ kind: 'free', text: searchValue.trim() } as PickerOption] : []),
		...filteredTags.map((tag): PickerOption => ({ kind: 'tag', tag }))
	]);

	$effect(() => {
		searchValue;
		activeIndex = 0;
	});

	$effect(() => {
		if (activeIndex >= options.length) activeIndex = Math.max(0, options.length - 1);
	});

	$effect(() => {
		tick().then(() => panelEl?.querySelector('input')?.focus());
	});

	function isSelected(tag: string): boolean {
		return selected.includes(tag);
	}

	function activate(option: PickerOption) {
		if (option.kind === 'tag') {
			if (!isSelected(option.tag) && !canAddMore) return;
			onToggle(option.tag);
			return;
		}
		if (!canAddMore) return;
		onAddCustom(option.text);
		searchValue = '';
	}

	function handleKeydown(event: KeyboardEvent) {
		switch (event.key) {
			case 'Escape':
				event.preventDefault();
				onClose();
				break;
			case 'ArrowDown':
				event.preventDefault();
				if (options.length) activeIndex = (activeIndex + 1) % options.length;
				break;
			case 'ArrowUp':
				event.preventDefault();
				if (options.length) activeIndex = (activeIndex - 1 + options.length) % options.length;
				break;
			case 'Enter':
				event.preventDefault();
				if (options[activeIndex]) activate(options[activeIndex]);
				break;
			case 'Backspace':
				if (searchValue === '') {
					event.preventDefault();
					onBackspaceEmpty();
				}
				break;
		}
	}

	function handleWindowPointerDown(event: PointerEvent) {
		const target = event.target as Node;
		if (panelEl?.contains(target)) return;
		onClose();
	}

	let verticalStyle = $derived(
		position.top !== undefined ? `top: ${position.top}px; bottom: auto;` : `bottom: ${position.bottom}px; top: auto;`
	);
</script>

<svelte:window onpointerdown={handleWindowPointerDown} />

<div use:portal style="display: contents;">
	<div
		bind:this={panelEl}
		class="fixed z-[99999] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2 rounded-xl border border-line bg-surface-1 p-3 shadow-floating"
		style="left: {position.left}px; {verticalStyle} max-height: {position.maxHeight}px;"
		role="dialog"
		aria-label="{category.label} picker"
	>
		<div class="flex items-center justify-between gap-2">
			<span class="text-sm font-medium text-fg">{category.label}</span>
			<span class="shrink-0 font-mono text-xs tabular-nums text-fg-subtle">{selected.length} / {category.tags.length}</span>
		</div>

		<Input
			bind:value={searchValue}
			onkeydown={handleKeydown}
			type="text"
			placeholder="Search {category.label.toLowerCase()}"
		/>

		<div class="flex max-h-80 flex-col overflow-y-auto">
			{#each options as option, i (option.kind === 'tag' ? option.tag : `__free__${option.text}`)}
				{@const active = i === activeIndex}
				{#if option.kind === 'tag'}
					{@const on = isSelected(option.tag)}
					<button
						type="button"
						onclick={() => activate(option)}
						disabled={!on && !canAddMore}
						class="flex items-center gap-2 rounded px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50
							{active ? 'bg-surface-2' : 'hover:bg-surface-2'}
							{on ? 'text-fg' : 'text-fg-muted'}"
					>
						<Icon name="check" className="w-3.5 h-3.5 shrink-0 {on ? 'text-signal' : 'invisible'}" />
						{option.tag}
					</button>
				{:else}
					<button
						type="button"
						disabled={!canAddMore}
						onclick={() => activate(option)}
						class="flex items-center gap-2 rounded border border-dashed border-line-strong px-3 py-2 text-left text-sm text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:opacity-50 {active
							? 'bg-surface-2'
							: ''}"
					>
						Add <strong class="text-fg">"{option.text}"</strong> as a custom {category.label.toLowerCase()} tag
					</button>
				{/if}
			{/each}
			{#if options.length === 0}
				<div class="px-3 py-2 text-sm text-fg-subtle">No {category.label.toLowerCase()} tags match "{searchValue}"</div>
			{/if}
		</div>

		<div class="flex items-center justify-between text-2xs text-fg-subtle">
			<span class="flex items-center gap-1"><Kbd keys="Enter" /> toggle</span>
			<span class="flex items-center gap-1"><Kbd keys="Esc" /> close</span>
		</div>
	</div>
</div>
