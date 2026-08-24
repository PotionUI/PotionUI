<script lang="ts">
	import type { Snippet } from 'svelte';

	type Variant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';
	type Size = 'sm' | 'md';

	let {
		variant = 'neutral',
		size = 'md',
		dot = false,
		class: className = '',
		children
	}: {
		variant?: Variant;
		size?: Size;
		dot?: boolean;
		class?: string;
		children?: Snippet;
	} = $props();

	// Written as full literal strings so Tailwind's class scanner can see them.
	const variantClasses: Record<Variant, string> = {
		neutral: 'bg-surface-2 text-fg-muted border border-line-strong',
		success: 'bg-success/10 text-success border border-success/25',
		warning: 'bg-warning/10 text-warning border border-warning/25',
		danger: 'bg-danger/10 text-danger border border-danger/25',
		info: 'bg-info/10 text-info border border-info/25',
		signal: 'bg-signal/10 text-signal border border-signal/25'
	};

	const dotClasses: Record<Variant, string> = {
		neutral: 'bg-fg-muted',
		success: 'bg-success',
		warning: 'bg-warning',
		danger: 'bg-danger',
		info: 'bg-info',
		signal: 'bg-signal'
	};

	const sizeClasses: Record<Size, string> = {
		sm: 'px-1.5 py-0 text-2xs',
		md: 'px-2 py-0.5 text-xs'
	};

	let classes = $derived(
		`inline-flex items-center gap-1 rounded font-medium ${sizeClasses[size]} ${variantClasses[variant]} ${className}`
	);
</script>

<span class={classes}>
	{#if dot}
		<span class="w-1.5 h-1.5 rounded-full {dotClasses[variant]}"></span>
	{/if}
	{@render children?.()}
</span>
