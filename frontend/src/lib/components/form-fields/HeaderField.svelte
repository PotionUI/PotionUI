<script lang="ts">
	export let config: any;

	const title = config.title || '';
	const variant = config.configuration?.variant || 'h3';
	const customStyle = config.configuration?.style || {};

	// Convert style object to CSS string
	function stylesToString(styles: Record<string, string>): string {
		return Object.entries(styles)
			.map(([key, value]) => {
				// Convert camelCase to kebab-case
				const kebabKey = key.replace(/([A-Z])/g, '-$1').toLowerCase();
				return `${kebabKey}: ${value}`;
			})
			.join('; ');
	}

	const styleString = stylesToString(customStyle);

	// Define classes for each variant
	const variantClasses: Record<string, string> = {
		h1: 'text-3xl font-semibold text-fg mb-4 mt-6',
		h2: 'text-2xl font-semibold text-fg mb-3 mt-5',
		h3: 'text-xl font-semibold text-fg mb-2 mt-4',
		h4: 'text-lg font-medium text-fg mb-2 mt-3',
		h5: 'text-base font-medium text-fg mb-1 mt-2',
		h6: 'text-sm font-medium text-fg mb-1 mt-2'
	};

	const className = variantClasses[variant] || variantClasses.h3;
</script>

{#if variant === 'h1'}
	<h1 class={className} style={styleString}>{title}</h1>
{:else if variant === 'h2'}
	<h2 class={className} style={styleString}>{title}</h2>
{:else if variant === 'h3'}
	<h3 class={className} style={styleString}>{title}</h3>
{:else if variant === 'h4'}
	<h4 class={className} style={styleString}>{title}</h4>
{:else if variant === 'h5'}
	<h5 class={className} style={styleString}>{title}</h5>
{:else if variant === 'h6'}
	<h6 class={className} style={styleString}>{title}</h6>
{:else}
	<h3 class={className} style={styleString}>{title}</h3>
{/if}
