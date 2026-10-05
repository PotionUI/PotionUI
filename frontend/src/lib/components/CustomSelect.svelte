<script lang="ts" context="module">
	let selectInstances = 0;
</script>

<script lang="ts">
	import { createEventDispatcher, tick } from 'svelte';
	import { computeSelectMenuPlacement, dockInsetFor } from '$lib/utils/menuPosition';
	import Icon from '$lib/components/Icon.svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';

	export let value: any = '';
	export let options: Array<{ value: any; label: string; description?: string; icon?: string }> = [];
	export let placeholder: string = 'Select an option...';
	export let disabled: boolean = false;
	export let searchable: boolean = false;
	export let size: 'sm' | 'md' | 'lg' = 'md';

	const dispatch = createEventDispatcher<{
		change: any;
	}>();

	let isDropdownOpen = false;
	let filterText = '';
	let containerRef: HTMLDivElement;
	let dropdownRef: HTMLDivElement;
	let inputRef: HTMLElement;
	let dropdownPosition = { top: 0, bottom: 0, left: 0, width: 0, openUpward: false, maxHeight: 256 };
	let activeIndex = 0;
	const optionIdPrefix = `custom-select-${++selectInstances}-option-`;
	$: activeOptionId = isDropdownOpen && filteredOptions[activeIndex] ? `${optionIdPrefix}${activeIndex}` : undefined;

	$: selectedOption = options.find((opt) => opt.value === value);
	$: displayValue = filterText || selectedOption?.label || '';

	$: filteredOptions = searchable && filterText
		? options.filter((option) =>
				option.label.toLowerCase().includes(filterText.toLowerCase())
			)
		: options;


	const MAX_MENU_HEIGHT = 256;
	const ROW_ESTIMATE = 44;

	function updateDropdownPosition() {
		if (!inputRef) return;
		const rect = inputRef.getBoundingClientRect();
		const gap = 4;
		const contentHeight = dropdownRef
			? dropdownRef.scrollHeight
			: Math.max(1, filteredOptions.length) * ROW_ESTIMATE;
		const placement = computeSelectMenuPlacement({
			triggerTop: rect.top,
			triggerBottom: rect.bottom,
			viewportHeight: window.innerHeight,
			bottomInset: dockInsetFor(inputRef),
			contentHeight,
			maxMenuHeight: MAX_MENU_HEIGHT,
			gap
		});
		dropdownPosition = {
			top: rect.bottom + gap,
			bottom: window.innerHeight - rect.top + gap,
			left: rect.left,
			width: rect.width,
			openUpward: placement.openUpward,
			maxHeight: placement.maxHeight
		};
	}

	async function openMenu() {
		updateDropdownPosition();
		isDropdownOpen = true;
		activeIndex = Math.max(0, filteredOptions.findIndex((o) => o.value === value));
		await tick();
		updateDropdownPosition();
		scrollActiveIntoView();
	}

	function scrollActiveIntoView() {
		dropdownRef?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: 'nearest' });
	}

	async function moveActive(next: number) {
		if (filteredOptions.length === 0) return;
		activeIndex = (next + filteredOptions.length) % filteredOptions.length;
		await tick();
		scrollActiveIntoView();
	}

	const sizeClasses = {
		sm: 'px-2 py-1 text-xs',
		md: 'px-3 py-2 text-sm min-h-9',
		lg: 'px-4 py-3 text-base'
	};

	const iconSizeClasses = {
		sm: 'w-3 h-3',
		md: 'w-4 h-4',
		lg: 'w-5 h-5'
	};

	function handleOptionSelect(optionValue: any) {
		value = optionValue;
		dispatch('change', optionValue);
		filterText = '';
		isDropdownOpen = false;
	}

	function handleInputClick() {
		if (disabled) return;
		if (isDropdownOpen) isDropdownOpen = false;
		else openMenu();
	}

	function handleInputInput(event: Event) {
		if (!searchable || disabled) return;
		const target = event.target as HTMLInputElement;
		filterText = target.value;
		openMenu();
		activeIndex = 0;
	}

	function handleClearFilter() {
		filterText = '';
		isDropdownOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		if (
			containerRef &&
			!containerRef.contains(event.target as Node) &&
			dropdownRef &&
			!dropdownRef.contains(event.target as Node)
		) {
			isDropdownOpen = false;
			filterText = '';
		}
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (disabled) return;

		if (event.key === 'Escape') {
			isDropdownOpen = false;
			filterText = '';
		} else if (event.key === 'ArrowDown') {
			event.preventDefault();
			if (!isDropdownOpen) openMenu();
			else moveActive(activeIndex + 1);
		} else if (event.key === 'ArrowUp' && isDropdownOpen) {
			event.preventDefault();
			moveActive(activeIndex - 1);
		} else if (event.key === 'Home' && isDropdownOpen && !searchable) {
			event.preventDefault();
			moveActive(0);
		} else if (event.key === 'End' && isDropdownOpen && !searchable) {
			event.preventDefault();
			moveActive(filteredOptions.length - 1);
		} else if (event.key === 'Enter' && isDropdownOpen && filteredOptions[activeIndex]) {
			event.preventDefault();
			handleOptionSelect(filteredOptions[activeIndex].value);
		}
	}
</script>

<svelte:window on:click={handleWindowClick} />

<div class="relative w-full" bind:this={containerRef}>
	<div class="relative" bind:this={inputRef}>
		{#if searchable}
			<input
				type="text"
				value={displayValue}
				on:input={handleInputInput}
				on:click={handleInputClick}
				on:keydown={handleKeyDown}
				{placeholder}
				{disabled}
				aria-expanded={isDropdownOpen}
				aria-haspopup="listbox"
				aria-activedescendant={activeOptionId}
				class="w-full {sizeClasses[
					size
				]} pr-8 bg-surface-2 border {isDropdownOpen
					? 'border-signal'
					: 'border-line-hover'} rounded text-fg placeholder-fg-subtle focus:outline-none focus:border-signal disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
			/>
		{:else}
			<button
				type="button"
				on:click={handleInputClick}
				on:keydown={handleKeyDown}
				{disabled}
				class="w-full {sizeClasses[
					size
				]} pr-8 flex flex-col justify-center text-left bg-surface-2 border {isDropdownOpen
					? 'border-signal'
					: 'border-line-hover'} rounded focus:outline-none focus:border-signal hover:bg-surface-3 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
				aria-expanded={isDropdownOpen}
				aria-haspopup="listbox"
				aria-activedescendant={activeOptionId}
			>
				{#if selectedOption}
					<span class="flex items-center gap-2 truncate text-fg">
						{#if selectedOption.icon}<Icon name={selectedOption.icon} className="w-3.5 h-3.5 flex-shrink-0 text-fg-muted" />{/if}
						<span class="truncate">{selectedOption.label}</span>
					</span>
					{#if selectedOption.description}
						<span class="block truncate text-xs text-fg-subtle mt-0.5">{selectedOption.description}</span>
					{/if}
				{:else}
					<span class="text-fg-subtle">{placeholder}</span>
				{/if}
			</button>
		{/if}

		<button
			type="button"
			class="absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg transition-colors"
			on:click={handleInputClick}
			on:keydown={handleKeyDown}
			disabled={disabled}
		>
			<svg
				class="{iconSizeClasses[size]} transition-transform {isDropdownOpen
					? 'rotate-180'
					: ''}"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		</button>
	</div>

</div>

{#if isDropdownOpen}
	<div
		use:portal
		use:overlayLayer
		bind:this={dropdownRef}
		data-dropdown="true"
		class="fixed z-overlay bg-surface-2 border border-line-hover rounded-xl shadow-overlay overflow-y-auto"
		role="listbox"
		style="{dropdownPosition.openUpward ? `bottom: ${dropdownPosition.bottom}px` : `top: ${dropdownPosition.top}px`}; left: {dropdownPosition.left}px; width: {dropdownPosition.width}px; max-height: {dropdownPosition.maxHeight}px;"
	>
		{#if filteredOptions.length === 0}
			<div class="px-4 py-3 text-sm text-fg-subtle text-center">No options found</div>
		{:else}
			{#each filteredOptions as option, index}
				<button
					type="button"
					id="{optionIdPrefix}{index}"
					class="w-full text-left px-4 py-2.5 transition-colors border-b border-line-strong last:border-b-0 {index === activeIndex
						? 'bg-surface-3'
						: option.value === value
							? 'bg-signal/10'
							: 'hover:bg-surface-3'}"
					on:click={() => handleOptionSelect(option.value)}
					role="option"
					data-active={index === activeIndex}
					aria-selected={option.value === value}
				>
					<div class="flex flex-col gap-1">
						<div class="flex items-center justify-between gap-2">
							<span
								class="flex min-w-0 flex-1 items-center gap-2 truncate text-sm font-medium {option.value === value
									? 'text-signal'
									: 'text-fg-muted'}"
							>
								{#if option.icon}<Icon name={option.icon} className="w-3.5 h-3.5 flex-shrink-0" />{/if}
								<span class="truncate">{option.label}</span>
							</span>
							{#if option.value === value}
								<svg
									class="w-4 h-4 text-signal flex-shrink-0"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M5 13l4 4L19 7"
									/>
								</svg>
							{/if}
						</div>
						{#if option.description}
							<span class="block truncate text-xs text-fg-subtle">{option.description}</span>
						{/if}
					</div>
				</button>
			{/each}
		{/if}
	</div>
{/if}
