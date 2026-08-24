<script lang="ts">
	type Size = 'sm' | 'md' | 'lg';

	let {
		checked = $bindable(false),
		onchange,
		onclick,
		disabled = false,
		busy = false,
		size = 'md',
		label,
		id,
		class: className = ''
	}: {
		checked?: boolean;
		onchange?: (checked: boolean) => void;
		onclick?: (e: MouseEvent) => void;
		disabled?: boolean;
		busy?: boolean;
		size?: Size;
		label: string;
		id?: string;
		class?: string;
	} = $props();

	const trackSizeClasses: Record<Size, string> = {
		sm: 'h-4 w-7',
		md: 'h-5 w-9',
		lg: 'h-6 w-11'
	};

	const thumbSizeClasses: Record<Size, string> = {
		sm: 'h-3 w-3',
		md: 'h-3.5 w-3.5',
		lg: 'h-4 w-4'
	};

	const thumbOffClasses: Record<Size, string> = {
		sm: 'translate-x-0.5',
		md: 'translate-x-0.5',
		lg: 'translate-x-1'
	};

	const thumbOnClasses: Record<Size, string> = {
		sm: 'translate-x-3.5',
		md: 'translate-x-4',
		lg: 'translate-x-6'
	};

	const spinnerSizeClasses: Record<Size, string> = {
		sm: 'w-2.5 h-2.5 border',
		md: 'w-3 h-3 border',
		lg: 'w-3.5 h-3.5 border-2'
	};

	let isDisabled = $derived(disabled || busy);

	function handleChange(e: Event) {
		checked = (e.currentTarget as HTMLInputElement).checked;
		onchange?.(checked);
	}
</script>

<span class="relative inline-flex flex-shrink-0 {trackSizeClasses[size]} {className}">
	<input
		type="checkbox"
		role="switch"
		{id}
		{checked}
		disabled={isDisabled}
		onchange={handleChange}
		{onclick}
		aria-label={label}
		aria-busy={busy || undefined}
		class="absolute inset-0 w-full h-full m-0 appearance-none opacity-0 rounded-full disabled:cursor-not-allowed {isDisabled
			? ''
			: 'cursor-pointer'}"
	/>
	<span
		aria-hidden="true"
		class="pointer-events-none absolute inset-0 rounded-full transition-colors {checked
			? 'bg-signal'
			: 'bg-surface-3 border border-line-strong'} {isDisabled ? 'opacity-50' : ''}"
	></span>
	<span
		aria-hidden="true"
		class="pointer-events-none absolute top-1/2 -translate-y-1/2 flex items-center justify-center rounded-full bg-canvas shadow transition-transform {thumbSizeClasses[
			size
		]} {checked ? thumbOnClasses[size] : thumbOffClasses[size]} {isDisabled ? 'opacity-50' : ''}"
	>
		{#if busy}
			<span
				class="rounded-full border-line-strong border-t-signal animate-spin {spinnerSizeClasses[size]}"
			></span>
		{/if}
	</span>
</span>
