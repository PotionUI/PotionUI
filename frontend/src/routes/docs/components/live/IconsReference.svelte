<script lang="ts">
	import { iconPaths, getAllIconNames } from '$lib/utils/IconLibrary';
	import { Input } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	interface IconEntry {
		name: string;
		path: string;
	}

	let filterText = '';
	let copiedIcon = '';
	let copyTimeout: ReturnType<typeof setTimeout> | null = null;

	const iconLibrary: IconEntry[] = getAllIconNames().map((name) => {
		const path = iconPaths[name];
		return {
			name,
			path: Array.isArray(path) ? path.join(' ') : path
		};
	});

	$: filteredIcons = iconLibrary.filter((icon) =>
		icon.name.toLowerCase().includes(filterText.toLowerCase())
	);

	function copyToClipboard(text: string, iconName: string) {
		navigator.clipboard.writeText(text).then(() => {
			copiedIcon = iconName;
			if (copyTimeout) clearTimeout(copyTimeout);
			copyTimeout = setTimeout(() => {
				copiedIcon = '';
			}, 2000);
		});
	}

	function copyIconSVG(icon: IconEntry) {
		const svg = `<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
	<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${icon.path}" />
</svg>`;
		copyToClipboard(svg, icon.name);
	}
</script>

<div class="mb-4">
	<Input
		type="search"
		bind:value={filterText}
		placeholder="Filter icons..."
		aria-label="Filter icons"
	/>
</div>

{#if filteredIcons.length === 0}
	<div class="bg-surface-2 rounded-lg border border-line p-12 text-center">
		<Icon name="sparkles" className="w-12 h-12 text-fg-subtle mx-auto mb-4" />
		<p class="text-fg-muted">No icons match your search</p>
	</div>
{:else}
	<div class="bg-surface-2 rounded-lg border border-line p-6 mb-4">
		<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-4">
			{#each filteredIcons as icon (icon.name)}
				<div class="group relative">
					<button
						on:click={() => copyIconSVG(icon)}
						class="w-full aspect-square flex flex-col items-center justify-center p-4 border border-line rounded-lg hover:border-line-hover hover:bg-surface-3 transition-all cursor-pointer"
						title="Click to copy SVG code for {icon.name}"
					>
						<svg
							class="w-8 h-8 mb-2 text-fg-muted group-hover:text-fg"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d={icon.path}
							/>
						</svg>
						<span class="text-2xs text-fg-muted group-hover:text-fg font-mono text-center break-all"
							>{icon.name}</span
						>

						{#if copiedIcon === icon.name}
							<div
								class="absolute inset-0 flex items-center justify-center bg-success/10 border-2 border-success/50 rounded-lg"
							>
								<div class="flex flex-col items-center gap-1">
									<Icon name="check" className="w-6 h-6 text-success" />
									<span class="text-xs font-medium text-success">Copied!</span>
								</div>
							</div>
						{/if}
					</button>
				</div>
			{/each}
		</div>
	</div>

	<div class="bg-surface-2 border border-line rounded-lg p-6">
		<h4
			class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted flex items-center gap-2 pb-3 border-b border-line mb-3"
		>
			<Icon name="info" className="w-4 h-4" />
			How to Use Icons
		</h4>
		<div class="space-y-2 text-sm text-fg">
			<p><strong>Click any icon</strong> to copy the SVG code to your clipboard</p>
			<p class="font-mono text-xs bg-surface-1 px-3 py-2 rounded border border-line">
				&lt;svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"&gt;<br />
				&nbsp;&nbsp;&lt;path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="..."
				/&gt;<br />
				&lt;/svg&gt;
			</p>
			<p class="mt-3">
				<strong>Size classes:</strong> w-4 h-4 (small) | w-5 h-5 (medium) | w-6 h-6 (large) | w-8 h-8
				(xl)
			</p>
			<p><strong>Color:</strong> Use text-* classes or stroke="currentColor" inherits text color</p>
			<p><strong>Animation:</strong> Add class="animate-spin" for loading icons</p>
		</div>
	</div>
{/if}
