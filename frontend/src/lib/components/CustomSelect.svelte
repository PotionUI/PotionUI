<script lang="ts">
	import { createEventDispatcher, onDestroy } from 'svelte';
	import portal from '$lib/actions/portal';

	// Props
	export let value: any = '';
	export let options: Array<{ value: any; label: string; description?: string }> = [];
	export let placeholder: string = 'Select an option...';
	export let disabled: boolean = false;
	export let searchable: boolean = false;
	export let size: 'sm' | 'md' | 'lg' = 'md';

	// Events
	const dispatch = createEventDispatcher<{
		change: any;
	}>();

	// State
	let isDropdownOpen = false;
	let filterText = '';
	let containerRef: HTMLDivElement;
	let dropdownRef: HTMLDivElement;
	let inputRef: HTMLElement;
	let dropdownPosition = { top: 0, bottom: 0, left: 0, width: 0, openUpward: false };

	// Reactive statements
	$: selectedOption = options.find((opt) => opt.value === value);
	$: displayValue = filterText || selectedOption?.label || '';

	$: filteredOptions = searchable && filterText
		? options.filter((option) =>
				option.label.toLowerCase().includes(filterText.toLowerCase())
			)
		: options;


	// Calculate dropdown position when opened
	// Automatically flips to open upward if not enough space below
	function updateDropdownPosition() {
		if (inputRef) {
			const rect = inputRef.getBoundingClientRect();
			const dropdownMaxHeight = 256; // max-h-64 = 16rem = 256px
			const viewportHeight = window.innerHeight;
			const spaceBelow = viewportHeight - rect.bottom;
			const spaceAbove = rect.top;
			const gap = 4;

			// Determine if we should open upward
			const openUpward = spaceBelow < dropdownMaxHeight && spaceAbove > spaceBelow;

			dropdownPosition = {
				top: rect.bottom + gap,
				bottom: viewportHeight - rect.top + gap,
				left: rect.left,
				width: rect.width,
				openUpward
			};
		}
	}

	// Size classes
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
		updateDropdownPosition();
		isDropdownOpen = !isDropdownOpen;
	}

	function handleInputInput(event: Event) {
		if (!searchable || disabled) return;
		const target = event.target as HTMLInputElement;
		filterText = target.value;
		updateDropdownPosition();
		isDropdownOpen = true;
	}

	function handleClearFilter() {
		filterText = '';
		isDropdownOpen = false;
	}

	// Click outside to close dropdown
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

	// Keyboard navigation
	function handleKeyDown(event: KeyboardEvent) {
		if (disabled) return;

		if (event.key === 'Escape') {
			isDropdownOpen = false;
			filterText = '';
		} else if (event.key === 'ArrowDown' && !isDropdownOpen) {
			isDropdownOpen = true;
		}
	}
</script>

<svelte:window on:click={handleWindowClick} />

<div class="relative w-full" bind:this={containerRef}>
	<!-- Input/Button -->
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
			>
				{#if selectedOption}
					<span class="block truncate text-fg">{selectedOption.label}</span>
					{#if selectedOption.description}
						<span class="block truncate text-xs text-fg-subtle mt-0.5">{selectedOption.description}</span>
					{/if}
				{:else}
					<span class="text-fg-subtle">{placeholder}</span>
				{/if}
			</button>
		{/if}

		<!-- Dropdown Arrow -->
		<button
			type="button"
			class="absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg transition-colors"
			on:click={handleInputClick}
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

<!-- Dropdown Menu - Rendered at body level using Svelte portal pattern -->
{#if isDropdownOpen}
	<div
		use:portal
		bind:this={dropdownRef}
		data-dropdown="true"
		class="fixed z-[9999] bg-surface-2 border border-line-hover rounded-xl shadow-overlay max-h-64 overflow-y-auto"
		role="listbox"
		style="{dropdownPosition.openUpward ? `bottom: ${dropdownPosition.bottom}px` : `top: ${dropdownPosition.top}px`}; left: {dropdownPosition.left}px; width: {dropdownPosition.width}px;"
	>
		{#if filteredOptions.length === 0}
			<div class="px-4 py-3 text-sm text-fg-subtle text-center">No options found</div>
		{:else}
			{#each filteredOptions as option}
				<button
					type="button"
					class="w-full text-left px-4 py-2.5 transition-colors border-b border-line-strong last:border-b-0 {option.value ===
					value
						? 'bg-signal/10'
						: 'hover:bg-surface-3'}"
					on:click={() => handleOptionSelect(option.value)}
					role="option"
					aria-selected={option.value === value}
				>
					<div class="flex flex-col gap-1">
						<div class="flex items-center justify-between gap-2">
							<span
								class="min-w-0 flex-1 truncate text-sm font-medium {option.value === value
									? 'text-signal'
									: 'text-fg-muted'}"
							>
								{option.label}
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
