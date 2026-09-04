<script module lang="ts">
	// Small icon set matching the shot-console mock's Feather glyphs
	// (console.html / shot-states.html <symbol> defs) verbatim -- the
	// project's Icon.svelte/IconLibrary is heroicon-based and doesn't carry
	// several of these (link, scissors, git-merge, corner-down-right,
	// refresh-cw, alert-triangle), so the console renders its own rather
	// than approximate them. Shared across the console/* leaves (b)(c)(d).
	//
	// Lives in a `module` script block (not the instance script) so it
	// compiles to a real named export of this `.svelte` module -- a type
	// exported from the instance script only becomes a component prop, never
	// a module export, which is what TS2614 was catching.
	export type ConsoleIconName =
		| 'chevron-down'
		| 'check'
		| 'more'
		| 'x'
		| 'zap'
		| 'link'
		| 'scissors'
		| 'git-merge'
		| 'corner-down-right'
		| 'refresh-cw'
		| 'alert-triangle'
		| 'package'
		| 'image'
		| 'layers'
		| 'clock'
		| 'plus'
		| 'grip';
</script>

<script lang="ts">
	let { name, class: className = 'h-3.5 w-3.5' }: { name: ConsoleIconName; class?: string } = $props();
</script>

<svg
	class={className}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.75"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	{#if name === 'chevron-down'}
		<polyline points="6 9 12 15 18 9" />
	{:else if name === 'check'}
		<polyline points="20 6 9 17 4 12" />
	{:else if name === 'more'}
		<circle cx="5" cy="12" r="1.5" />
		<circle cx="12" cy="12" r="1.5" />
		<circle cx="19" cy="12" r="1.5" />
	{:else if name === 'x'}
		<line x1="18" y1="6" x2="6" y2="18" />
		<line x1="6" y1="6" x2="18" y2="18" />
	{:else if name === 'zap'}
		<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
	{:else if name === 'link'}
		<path d="M15 7h3a5 5 0 0 1 5 5 5 5 0 0 1-5 5h-3m-6 0H6a5 5 0 0 1-5-5 5 5 0 0 1 5-5h3" />
		<line x1="8" y1="12" x2="16" y2="12" />
	{:else if name === 'scissors'}
		<circle cx="6" cy="6" r="3" />
		<circle cx="6" cy="18" r="3" />
		<line x1="20" y1="4" x2="8.12" y2="15.88" />
		<line x1="14.47" y1="14.48" x2="20" y2="20" />
		<line x1="8.12" y1="8.12" x2="12" y2="12" />
	{:else if name === 'git-merge'}
		<circle cx="18" cy="18" r="3" />
		<circle cx="6" cy="6" r="3" />
		<path d="M6 21V9a9 9 0 0 0 9 9" />
	{:else if name === 'corner-down-right'}
		<polyline points="15 10 20 15 15 20" />
		<path d="M4 4v7a4 4 0 0 0 4 4h12" />
	{:else if name === 'refresh-cw'}
		<polyline points="23 4 23 10 17 10" />
		<polyline points="1 20 1 14 7 14" />
		<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
	{:else if name === 'alert-triangle'}
		<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
		<line x1="12" y1="9" x2="12" y2="13" />
		<line x1="12" y1="17" x2="12.01" y2="17" />
	{:else if name === 'package'}
		<path
			d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"
		/>
		<polyline points="3.27 6.96 12 12.01 20.73 6.96" />
		<line x1="12" y1="22.08" x2="12" y2="12" />
	{:else if name === 'image'}
		<rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
		<circle cx="8.5" cy="8.5" r="1.5" />
		<polyline points="21 15 16 10 5 21" />
	{:else if name === 'layers'}
		<polygon points="12 2 2 7 12 12 22 7 12 2" />
		<polyline points="2 17 12 22 22 17" />
		<polyline points="2 12 12 17 22 12" />
	{:else if name === 'clock'}
		<circle cx="12" cy="12" r="10" />
		<polyline points="12 6 12 12 16 14" />
	{:else if name === 'plus'}
		<line x1="12" y1="5" x2="12" y2="19" />
		<line x1="5" y1="12" x2="19" y2="12" />
	{:else if name === 'grip'}
		<circle cx="9" cy="6" r="1" />
		<circle cx="9" cy="12" r="1" />
		<circle cx="9" cy="18" r="1" />
		<circle cx="15" cy="6" r="1" />
		<circle cx="15" cy="12" r="1" />
		<circle cx="15" cy="18" r="1" />
	{/if}
</svg>
