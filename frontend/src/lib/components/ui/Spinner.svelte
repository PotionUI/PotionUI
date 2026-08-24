<script lang="ts">
	type Size = 'sm' | 'md' | 'lg';

	let { size = 'md' }: { size?: Size } = $props();

	const sizeClasses: Record<Size, string> = {
		sm: 'w-4 h-4 border-2',
		md: 'w-6 h-6 border-2',
		lg: 'w-8 h-8 border-[3px]'
	};
</script>

<span
	class="inline-block rounded-full border-line-strong animate-spin {sizeClasses[size]}"
	style="border-top-color: rgb(var(--accent));"
	role="status"
	aria-label="Loading"
></span>
