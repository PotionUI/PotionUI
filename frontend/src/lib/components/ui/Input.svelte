<script lang="ts">
	let {
		value = $bindable(''),
		invalid = false,
		class: className = '',
		...rest
	}: {
		value?: string;
		invalid?: boolean;
		class?: string;
		[key: string]: unknown;
	} = $props();

	let classes = $derived(
		`input ${invalid ? 'border-danger focus:ring-danger' : ''} ${className}`
	);
</script>

<input class={classes} bind:value {...rest} />
