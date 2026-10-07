<script lang="ts">
	type Size = 'sm' | 'md' | 'lg';

	let {
		username,
		avatar = null,
		size = 'md',
		dim = false
	}: { username: string; avatar?: string | null; size?: Size; dim?: boolean } = $props();

	let broken = $state(false);

	const sizeClasses: Record<Size, string> = {
		sm: 'w-7 h-7 text-xs',
		md: 'w-9 h-9 text-sm',
		lg: 'w-12 h-12 text-base'
	};

	let showImage = $derived(!!avatar && !broken);

	$effect(() => {
		if (avatar) broken = false;
	});
</script>

<span
	class="relative flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold text-accent-contrast {sizeClasses[
		size
	]} {showImage ? '' : dim ? 'bg-fg-subtle' : 'bg-accent'} {dim ? 'opacity-70' : ''}"
	data-testid="account-avatar"
>
	{#if showImage}
		<img src={avatar} alt="" class="h-full w-full object-cover" onerror={() => (broken = true)} />
	{:else}
		{username.charAt(0).toUpperCase()}
	{/if}
</span>
